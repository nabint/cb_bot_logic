# -----------
# Callbreak MCTS Simulation using C engine.
# Same logic as callbreak_mcts/main.py, but uses callbreak_mcts_c/mcts_bridge
# for MCTS search (C shared library via ctypes).
#
# Build first: cd callbreak_mcts_c && make
# Run:         python3 -m callbreak_mcts_c.main 10
# -----------

import random
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bid_logic import Suit, SUIT_MAP, BidLogic, GameMode
from bot_logic import BotLogic, Player, GameHelper

# Import C-backed MCTS search via the bridge
from callbreak_mcts_c.mcts_bridge import mcts_search, bot_logic_select_card
from callbreak_mcts.dynamic_bid import estimate_mcts_bid

# Reuse utils from the Python version (get_winning_card, track_void_suits, get_legal_cards)
from callbreak_mcts.utils import (
    get_legal_cards,
    get_winning_card,
    track_void_suits,
)

# -------------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------------
NUM_GAMES = 100
NUM_ROUNDS_PER_GAME = 5
NUM_PLAYERS = 4
CARDS_PER_HAND = 13
# MCTS_PLAYERS = {0, 2}
# RULE_PLAYERS = {1, 3}
MCTS_PLAYERS = {1, 3}
RULE_PLAYERS = {0, 2}
MCTS_ITERATIONS = 1200
MCTS_SIMS_PER_DET = 1
MCTS_TIME_LIMIT_MS = None
BLOCK_LEADER = True

RANK_NAMES = {
    14: "A", 13: "K", 12: "Q", 11: "J",
    10: "10", 9: "9", 8: "8", 7: "7",
    6: "6", 5: "5", 4: "4", 3: "3", 2: "2",
}
SUIT_SYMBOLS = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}


def card_display(card_str):
    suit_char = card_str[-1]
    rank = int(card_str[:-1])
    return f"{RANK_NAMES.get(rank, str(rank))}{SUIT_SYMBOLS.get(suit_char, suit_char)}"


def create_deck():
    deck = []
    for s in ["D", "C", "H", "S"]:
        for r in range(2, 15):
            deck.append(f"{r}{s}")
    return deck


def deal_cards(deck):
    random.shuffle(deck)
    hands = [[] for _ in range(NUM_PLAYERS)]
    for i, card in enumerate(deck):
        hands[i % NUM_PLAYERS].append(card)
    return hands


def calculate_score(bid, tricks_won):
    if tricks_won >= bid:
        return bid + 0.1 * (tricks_won - bid)
    else:
        return -bid


def _mcts_bid_card_picker(**kwargs):
    selected, _ = mcts_search(
        original_deck=kwargs["original_deck"],
        known_hand=kwargs["known_hand"],
        bids=kwargs["bids"],
        tricks_won=kwargs["tricks_won"],
        current_turn=kwargs["current_turn"],
        cards_played=kwargs["cards_played"],
        trick_starter=kwargs["trick_starter"],
        discard_pile=kwargs["discard_pile"],
        led_suit=kwargs["led_suit"],
        void_tracker=kwargs["void_tracker"],
        dealer_index=kwargs["dealer_index"],
        player_index=kwargs["player_index"],
        iterations=kwargs["iterations"],
        simulations_per_det=kwargs["simulations_per_det"],
        time_limit_ms=kwargs["time_limit_ms"],
        block_leader=kwargs["block_leader"],
        cumulative_scores=kwargs["cumulative_scores"],
        current_round=kwargs["current_round"],
        total_rounds=kwargs["total_rounds"],
        discard_starters=kwargs["discard_starters"],
        player_in_game_ids=kwargs["player_in_game_ids"],
    )
    return selected


def _other_card_picker(**kwargs):
    return bot_logic_select_card(
        known_hand=kwargs["current_player"].cards[:],
        bids=kwargs["bids"],
        tricks_won=kwargs["tricks_won"],
        current_turn=kwargs["current_idx"],
        cards_played=kwargs["played_cards"],
        trick_starter=kwargs["starter_idx"],
        discard_pile=kwargs["discard_pile"],
        led_suit=kwargs["led_suit"],
        dealer_index=kwargs["dealer_index"],
        player_index=kwargs["current_idx"],
        discard_starters=kwargs["discard_starters"],
    )


def select_bid_for_player(players, bot_logics, dealer, player_index, cumulative_scores, game_round):
    fallback_bid = max(1, bot_logics[player_index].compute_bid())
    if player_index not in MCTS_PLAYERS:
        return fallback_bid

    try:
        return estimate_mcts_bid(
            player_index=player_index,
            known_hand=players[player_index].cards[:],
            known_bids=[player.bid for player in players],
            dealer_index=dealer.in_game_id,
            cumulative_scores=cumulative_scores[:],
            current_round=game_round,
            total_rounds=NUM_ROUNDS_PER_GAME,
            mcts_card_picker=_mcts_bid_card_picker,
            base_iterations=MCTS_ITERATIONS,
            base_simulations_per_det=MCTS_SIMS_PER_DET,
            time_limit_ms=MCTS_TIME_LIMIT_MS,
            block_leader=False,
            rule_card_picker=_other_card_picker,
            mcts_player_indices=MCTS_PLAYERS,
            player_in_game_ids=[player.in_game_id for player in players],
        )
    except Exception:
        return fallback_bid


# -------------------------------------------------------------------------
# Single game simulation
# -------------------------------------------------------------------------
def play_one_game(verbose=False):
    game_helper = GameHelper()
    cumulative_scores = [0.0] * NUM_PLAYERS

    for game_round in range(1, NUM_ROUNDS_PER_GAME + 1):
        deck = create_deck()
        original_deck = deck[:]
        hands = deal_cards(deck)

        players = []
        bot_logics = {}

        for i in range(NUM_PLAYERS):
            p = Player(i, f"P{i}")
            p.cards = hands[i]
            p.bid = 0
            p.hands = 0
            players.append(p)

        for i in range(NUM_PLAYERS):
            bot = BotLogic(players[i], game_helper, players)
            bot.on_deal_completed()
            bot_logics[i] = bot

        # Bidding
        dealer = players[(game_round - 1) % NUM_PLAYERS]
        for bid_turn in range(NUM_PLAYERS):
            bp = game_helper.get_bid_player(dealer, players, bid_turn)
            bid = select_bid_for_player(
                players,
                bot_logics,
                dealer,
                bp.in_game_id,
                cumulative_scores,
                game_round,
            )
            bp.bid = max(1, bid)

        bids = [p.bid for p in players]

        if verbose:
            print(f"\n  Round {game_round} | Bids: {bids}")
            for p in players:
                cards_str = " ".join(card_display(c) for c in sorted(
                    p.cards, key=lambda c: ({"S":0,"H":1,"C":2,"D":3}.get(c[-1],4), -int(c[:-1]))
                ))
                print(f"    {p.username}: {cards_str}")

        # Play 13 tricks
        turn_starter_idx = game_helper.get_bid_player(dealer, players, 0).in_game_id
        tricks_won = [0] * NUM_PLAYERS
        discard_pile = []
        discard_starters = []
        void_tracker = [set() for _ in range(4)]

        for play_turn in range(CARDS_PER_HAND):
            played_cards = []
            starter_idx = turn_starter_idx

            for idx in bot_logics:
                bot_logics[idx].on_throw_turn_started(
                    game_round, play_turn, 0, players[starter_idx]
                )

            for throw_turn in range(NUM_PLAYERS):
                current_idx = (starter_idx + throw_turn) % NUM_PLAYERS
                current_player = players[current_idx]

                led_suit = SUIT_MAP[played_cards[0][-1]] if played_cards else None
                legal = get_legal_cards(current_player.cards, played_cards, led_suit)
                current_player.legal_cards = legal

                if current_idx in MCTS_PLAYERS:
                    
                    if len(legal) == 1:
                        selected = legal[0]
                    else:
                        # Use C engine via mcts_bridge
                        selected, _ = mcts_search(
                            original_deck=original_deck,
                            known_hand=current_player.cards[:],
                            bids=bids,
                            tricks_won=tricks_won,
                            current_turn=current_idx,
                            cards_played=played_cards[:],
                            trick_starter=starter_idx,
                            discard_pile=discard_pile[:],
                            led_suit=led_suit,
                            void_tracker=void_tracker,
                            dealer_index=dealer.in_game_id,
                            player_index=current_idx,
                            iterations=MCTS_ITERATIONS,
                            simulations_per_det=MCTS_SIMS_PER_DET,
                            time_limit_ms=MCTS_TIME_LIMIT_MS,
                            block_leader=False,# BLOCK_LEADER,
                            cumulative_scores=cumulative_scores[:],
                            current_round=game_round,
                            total_rounds=NUM_ROUNDS_PER_GAME,
                            discard_starters=discard_starters[:],
                            player_in_game_ids=[p.in_game_id for p in players],
                        )
                else:
                    # selected, _ = mcts_search(
                    #         original_deck=original_deck,
                    #         known_hand=current_player.cards[:],
                    #         bids=bids,
                    #         tricks_won=tricks_won,
                    #         current_turn=current_idx,
                    #         cards_played=played_cards[:],
                    #         trick_starter=starter_idx,
                    #         discard_pile=discard_pile[:],
                    #         led_suit=led_suit,
                    #         void_tracker=void_tracker,
                    #         dealer_index=dealer.in_game_id,
                    #         player_index=current_idx,
                    #         iterations=MCTS_ITERATIONS,
                    #         simulations_per_det=MCTS_SIMS_PER_DET,
                    #         time_limit_ms=MCTS_TIME_LIMIT_MS,
                    #         block_leader=False,# BLOCK_LEADER,
                    #         cumulative_scores=cumulative_scores[:],
                    #         current_round=game_round,
                    #         total_rounds=NUM_ROUNDS_PER_GAME,
                    #         discard_starters=discard_starters[:],
                    #         player_in_game_ids=[p.in_game_id for p in players],
                    #     )
                    
                    selected = bot_logic_select_card(
                        known_hand=current_player.cards[:],
                        bids=bids,
                        tricks_won=tricks_won,
                        current_turn=current_idx,
                        cards_played=played_cards[:],
                        trick_starter=starter_idx,
                        discard_pile=discard_pile[:],
                        led_suit=led_suit,
                        dealer_index=dealer.in_game_id,
                        player_index=current_idx,
                        discard_starters=discard_starters[:],
                    )

                if selected not in legal:
                    selected = random.choice(legal)

                played_cards.append(selected)
                current_player.cards.remove(selected)
                discard_pile.append(selected)

                for idx in bot_logics:
                    bot_logics[idx].on_throw_card_selected(
                        selected, game_round, play_turn, throw_turn, current_player
                    )

            # Resolve trick
            winning_card, win_idx = get_winning_card(played_cards)
            winner_idx = (starter_idx + win_idx) % NUM_PLAYERS
            tricks_won[winner_idx] += 1
            players[winner_idx].hands += 1
            discard_starters.append(starter_idx)

            track_void_suits(played_cards, starter_idx, void_tracker)

            last_player_idx = (starter_idx + 3) % NUM_PLAYERS
            for idx in bot_logics:
                bot_logics[idx].on_throw_turn_completed(
                    3, players[last_player_idx], played_cards
                )

            if verbose:
                cards_disp = " ".join(card_display(c) for c in played_cards)
                print(f"    Trick {play_turn+1}: {cards_disp} → P{winner_idx} wins")

            turn_starter_idx = winner_idx

        assert sum(tricks_won) == CARDS_PER_HAND

        for i in range(NUM_PLAYERS):
            cumulative_scores[i] += calculate_score(bids[i], tricks_won[i])

        if verbose:
            print(f"  Tricks: {tricks_won} | Bids: {bids}")
            for i in range(NUM_PLAYERS):
                tag = "MCTS" if i in MCTS_PLAYERS else "RULE"
                print(f"    P{i} [{tag}]: {tricks_won[i]}/{bids[i]} "
                      f"(round: {calculate_score(bids[i], tricks_won[i]):+.1f}, "
                      f"total: {cumulative_scores[i]:.1f})")

        for idx in bot_logics:
            bot_logics[idx].on_game_round_completed(game_round)

    return cumulative_scores


# -------------------------------------------------------------------------
# Run simulation
# -------------------------------------------------------------------------
def run_simulation(num_games=NUM_GAMES, verbose_interval=100):
    print("=" * 60)
    print("  CALLBREAK MCTS (C ENGINE) vs RULE-BASED SIMULATION")
    print("=" * 60)
    print(f"  Games: {num_games} | Rounds/game: {NUM_ROUNDS_PER_GAME}")
    print(f"  MCTS: {', '.join('P%d' % i for i in sorted(MCTS_PLAYERS))} | Rule: {', '.join('P%d' % i for i in sorted(RULE_PLAYERS))}")
    print(f"  MCTS: {MCTS_ITERATIONS} dets × {MCTS_SIMS_PER_DET} sims/det"
          f" = {MCTS_ITERATIONS * MCTS_SIMS_PER_DET} rollouts/decision")
    print(f"  Engine: C (via ctypes)")
    print("=" * 60)

    mcts_wins = 0
    rule_wins = 0
    mcts_total_score = 0.0
    rule_total_score = 0.0
    player_wins = [0] * NUM_PLAYERS
    player_total_scores = [0.0] * NUM_PLAYERS

    start_time = time.time()

    for game_num in range(1, num_games + 1):
        scores = play_one_game(verbose=False)

        for i in range(NUM_PLAYERS):
            player_total_scores[i] += scores[i]

        max_score = max(scores)
        winners = [i for i in range(NUM_PLAYERS) if scores[i] == max_score]
        for w in winners:
            player_wins[w] += 1

        best_idx = scores.index(max(scores))
        if best_idx in MCTS_PLAYERS:
            mcts_wins += 1
        else:
            rule_wins += 1

        mcts_total_score += sum(scores[i] for i in MCTS_PLAYERS)
        rule_total_score += sum(scores[i] for i in RULE_PLAYERS)

        if game_num % verbose_interval == 0:
            elapsed = time.time() - start_time
            rate = game_num / elapsed if elapsed > 0 else 0
            print(f"  [{game_num:4d}/{num_games}] MCTS: {mcts_wins} | "
                  f"Rule: {rule_wins} | {rate:.1f} games/sec")

    elapsed = time.time() - start_time

    print()
    print("=" * 60)
    print("  FINAL RESULTS")
    print("=" * 60)

    total = mcts_wins + rule_wins
    mcts_pct = mcts_wins / total * 100 if total else 50
    rule_pct = rule_wins / total * 100 if total else 50

    print(f"\n  MCTS wins: {mcts_wins:4d} ({mcts_pct:.1f}%)")
    print(f"  Rule wins: {rule_wins:4d} ({rule_pct:.1f}%)")
    print(f"  MCTS avg score: {mcts_total_score / num_games:.2f}")
    print(f"  Rule avg score: {rule_total_score / num_games:.2f}")

    print(f"\n  {'Player':<8} {'Type':<6} {'Wins':>5} {'Win%':>6} {'AvgScore':>9}")
    print(f"  {'─' * 40}")
    for i in range(NUM_PLAYERS):
        ptype = "MCTS" if i in MCTS_PLAYERS else "Rule"
        avg = player_total_scores[i] / num_games
        pct = player_wins[i] / num_games * 100
        print(f"  P{i:<7} {ptype:<6} {player_wins[i]:>5} {pct:>5.1f}% {avg:>+8.2f}")

    print(f"\n  Time: {elapsed:.1f}s ({num_games / elapsed:.1f} games/sec)")
    print("=" * 60)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    verbose = "--verbose" in sys.argv

    n_games = int(args[0]) if args else NUM_GAMES

    if len(args) > 1:
        random.seed(int(args[1]))
        print(f"Using random seed: {args[1]}")

    if verbose:
        play_one_game(verbose=True)
    else:
        run_simulation(num_games=n_games)
