import os
import random
import sys
from collections import Counter

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from bid_logic import Suit, SUIT_MAP
from mcts_bridge import determinize_hidden_hands, get_legal_cards_c


NUM_PLAYERS = 4
CARDS_PER_HAND = 13
SUITS = ["D", "C", "H", "S"]
FULL_DECK = [f"{rank}{suit}" for suit in SUITS for rank in range(2, 15)]


def card_suit(card):
    return SUIT_MAP[card[-1]]


def card_rank(card):
    return int(card[:-1])


def canonical_legal_cards(hand, played_cards):
    if not played_cards:
        return hand[:]

    led_suit = card_suit(played_cards[0])
    suit_cards = {Suit.SPADES: [], Suit.HEARTS: [], Suit.DIAMONDS: [], Suit.CLUBS: []}
    for card in hand:
        suit_cards[card_suit(card)].append(card)

    if len(played_cards) == 1:
        if not suit_cards[led_suit]:
            if led_suit == Suit.SPADES:
                return hand[:]
            if suit_cards[Suit.SPADES]:
                return suit_cards[Suit.SPADES][:]
            return hand[:]

        higher = [c for c in suit_cards[led_suit] if card_rank(c) > card_rank(played_cards[0])]
        return higher if higher else suit_cards[led_suit][:]

    def is_trumped(cards):
        if card_suit(cards[0]) == Suit.SPADES:
            return False
        return any(card_suit(c) == Suit.SPADES for c in cards[1:])

    def highest_rank(cards, suit):
        best = -1
        for c in cards:
            if card_suit(c) == suit:
                best = max(best, card_rank(c))
        return best

    spades = suit_cards[Suit.SPADES]
    led_cards = suit_cards[led_suit]

    if led_suit == Suit.SPADES:
        if not spades:
            return hand[:]
        higher = [c for c in spades if card_rank(c) > highest_rank(played_cards, Suit.SPADES)]
        return higher if higher else spades[:]

    if is_trumped(played_cards):
        if led_cards:
            return led_cards[:]
        if not spades:
            return hand[:]
        higher = [c for c in spades if card_rank(c) > highest_rank(played_cards, Suit.SPADES)]
        return higher if higher else hand[:]

    if not led_cards:
        return spades[:] if spades else hand[:]

    higher = [c for c in led_cards if card_rank(c) > highest_rank(played_cards, led_suit)]
    return higher if higher else led_cards[:]


def compare_legal_cards(sample_count=15000, seed=7):
    random.seed(seed)
    mismatches = []

    fixed_cases = [
        (["13S", "4S", "7C"], ["10H", "3S"]),
        (["6S", "4S", "7C"], ["10H", "13S"]),
        (["2S", "5H", "9C"], ["3S"]),
        (["4D", "5H", "7C"], ["8D"]),
        (["14S", "5S", "4S", "10H"], ["8S"]),
    ]

    def run_case(hand, played):
        led = card_suit(played[0]) if played else None
        c_legal = sorted(get_legal_cards_c(hand, played, led))
        py_legal = sorted(canonical_legal_cards(hand, played))
        if c_legal != py_legal:
            mismatches.append((hand, played, c_legal, py_legal))

    for hand, played in fixed_cases:
        run_case(hand, played)

    for _ in range(sample_count):
        deck = FULL_DECK[:]
        random.shuffle(deck)
        played_count = random.randint(0, 3)
        hand_size = random.randint(1, 13)
        played = deck[:played_count]
        hand = deck[played_count:played_count + hand_size]
        run_case(hand, played)

    return mismatches


def expected_hand_sizes(known_hand_size, tricks_won, cards_played, trick_starter, player_index):
    complete_tricks = sum(tricks_won)
    sizes = [0] * NUM_PLAYERS
    for i in range(NUM_PLAYERS):
        if i == player_index:
            sizes[i] = known_hand_size
            continue
        played_in_trick = 0
        for j in range(len(cards_played)):
            if (trick_starter + j) % NUM_PLAYERS == i:
                played_in_trick = 1
                break
        sizes[i] = CARDS_PER_HAND - complete_tricks - played_in_trick
    return sizes


def validate_determinization(args, hands):
    problems = []
    expected_sizes = expected_hand_sizes(
        len(args["known_hand"]),
        args["tricks_won"],
        args["cards_played"],
        args["trick_starter"],
        args["player_index"],
    )

    seen = set(args["discard_pile"]) | set(args["cards_played"])
    total = list(args["discard_pile"]) + list(args["cards_played"])

    for p in range(NUM_PLAYERS):
        hand = hands[p]
        if len(hand) != expected_sizes[p]:
            problems.append(f"hand_size_mismatch:p{p}:{len(hand)}!={expected_sizes[p]}")
        if p == args["player_index"] and sorted(hand) != sorted(args["known_hand"]):
            problems.append("root_hand_changed")

        voids = set(args["void_tracker"][p])
        for card in hand:
            if card in seen:
                problems.append(f"known_card_reassigned:{card}")
            if card_suit(card) in voids:
                problems.append(f"void_violation:p{p}:{card}")
            seen.add(card)
            total.append(card)

    if len(total) != 52:
        problems.append(f"total_card_count:{len(total)}")
    if len(set(total)) != 52:
        dupes = [card for card, count in Counter(total).items() if count > 1]
        problems.append(f"duplicate_cards:{dupes}")

    return problems


def sample_many(args, sample_count):
    results = []
    for _ in range(sample_count):
        results.append(determinize_hidden_hands(**args))
    return results


def mean_counter(samples, player_idx, predicate):
    total = 0.0
    for hands in samples:
        total += sum(1 for card in hands[player_idx] if predicate(card))
    return total / float(len(samples))


def frequency(samples, player_idx, predicate):
    total = 0
    for hands in samples:
        total += 1 if any(predicate(card) for card in hands[player_idx]) else 0
    return total / float(len(samples))


def run_determinization_audit(sample_count=400):
    report = {}

    legality_args = {
        "original_deck": FULL_DECK,
        "known_hand": ["2D", "3D", "4D", "2C", "3C", "4C", "2H", "3H", "4H", "2S", "3S", "4S", "5C"],
        "bids": [3, 4, 2, 1],
        "tricks_won": [0, 0, 0, 0],
        "current_turn": 0,
        "cards_played": [],
        "trick_starter": 0,
        "discard_pile": [],
        "discard_starters": [],
        "led_suit": None,
        "void_tracker": [set(), set(), set(), set()],
        "dealer_index": 3,
        "player_index": 0,
        "player_in_game_ids": [11, 22, 33, 44],
    }
    legality_samples = sample_many(legality_args, sample_count)
    legality_problems = []
    for hands in legality_samples:
        legality_problems.extend(validate_determinization(legality_args, hands))
    report["legality_problem_count"] = len(legality_problems)
    report["legality_problem_examples"] = legality_problems[:5]

    void_args = dict(legality_args)
    void_args["void_tracker"] = [set(), set(), {Suit.DIAMONDS}, set()]
    void_samples = sample_many(void_args, sample_count)
    report["void_player2_avg_diamonds"] = mean_counter(
        void_samples, 2, lambda c: card_suit(c) == Suit.DIAMONDS
    )

    max_rank_args = {
        "original_deck": FULL_DECK,
        "known_hand": ["2D", "3D", "4D", "2C", "3C", "4C", "2H", "3H", "4H", "2S", "3S", "4S"],
        "bids": [3, 3, 3, 3],
        "tricks_won": [0, 0, 0, 1],
        "current_turn": 0,
        "cards_played": [],
        "trick_starter": 0,
        "discard_pile": ["10H", "8H", "12H", "2S"],
        "discard_starters": [],
        "led_suit": None,
        "void_tracker": [set(), set(), set(), set()],
        "dealer_index": 3,
        "player_index": 0,
        "player_in_game_ids": [11, 22, 33, 44],
    }
    max_rank_samples = sample_many(max_rank_args, sample_count)
    report["player1_freq_heart_gt_10"] = frequency(
        max_rank_samples, 1, lambda c: c in {"11H", "13H", "14H"}
    )

    bid_high_args = dict(legality_args)
    bid_high_args["bids"] = [1, 6, 1, 1]
    bid_low_args = dict(legality_args)
    bid_low_args["bids"] = [1, 1, 1, 1]
    high_samples = sample_many(bid_high_args, sample_count)
    low_samples = sample_many(bid_low_args, sample_count)
    strong_pred = lambda c: c in {"14S", "13S", "12S", "11S", "14H", "14D", "14C"}
    report["bid_high_player1_avg_strong"] = mean_counter(high_samples, 1, strong_pred)
    report["bid_low_player1_avg_strong"] = mean_counter(low_samples, 1, strong_pred)

    context_hearts_args = dict(legality_args)
    context_hearts_args["cards_played"] = ["9H"]
    context_hearts_args["trick_starter"] = 1
    context_hearts_args["current_turn"] = 2
    context_hearts_args["led_suit"] = Suit.HEARTS
    context_clubs_args = dict(context_hearts_args)
    context_clubs_args["cards_played"] = ["9C"]
    context_clubs_args["led_suit"] = Suit.CLUBS
    hearts_samples = sample_many(context_hearts_args, sample_count)
    clubs_samples = sample_many(context_clubs_args, sample_count)
    report["context_hearts_player2_freq_AH"] = frequency(
        hearts_samples, 2, lambda c: c == "14H"
    )
    report["context_clubs_player2_freq_AH"] = frequency(
        clubs_samples, 2, lambda c: c == "14H"
    )

    failures = []
    if report["legality_problem_count"] != 0:
        failures.append("determinization_legality")
    if report["void_player2_avg_diamonds"] != 0.0:
        failures.append("void_not_enforced")
    if report["player1_freq_heart_gt_10"] != 0.0:
        failures.append("max_rank_not_enforced")
    if report["bid_high_player1_avg_strong"] <= report["bid_low_player1_avg_strong"]:
        failures.append("bid_signal_not_increasing_strong_cards")
    if report["context_hearts_player2_freq_AH"] <= report["context_clubs_player2_freq_AH"]:
        failures.append("current_trick_context_not_increasing_led_suit")

    return report, failures


def main():
    mismatches = compare_legal_cards()
    print("LegalCards parity:")
    print(f"  random_cases_checked: {15000}")
    print(f"  mismatches: {len(mismatches)}")
    for idx, mismatch in enumerate(mismatches[:5], 1):
        hand, played, c_legal, py_legal = mismatch
        print(f"  mismatch_{idx}: hand={hand} played={played} c={c_legal} godot={py_legal}")

    report, failures = run_determinization_audit()
    print("Determinization audit:")
    for key, value in report.items():
        print(f"  {key}: {value}")

    all_failures = []
    if mismatches:
        all_failures.append("legal_cards_parity")
    all_failures.extend(failures)

    if all_failures:
        print("Audit failed:")
        for failure in all_failures:
            print(f"  - {failure}")
        sys.exit(1)

    print("Audit passed.")


if __name__ == "__main__":
    main()
