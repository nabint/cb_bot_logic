# -----------
# Utility helpers for Callbreak MCTS.
# Card format: string like '14S', '2D', '11H' etc.
# -----------

import random

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bid_logic import Suit, SUIT_MAP


# -------------------------------------------------------------------------
# Card diffing
# -------------------------------------------------------------------------
def card_list_diff(list1, list2):
    """Return cards in list1 that are not in list2 (set difference)."""
    exclude = set(list2)
    return [card for card in list1 if card not in exclude]


# -------------------------------------------------------------------------
# Legal cards (Callbreak rules)
# -------------------------------------------------------------------------
def get_legal_cards(hand, played_cards, led_suit=None):
    """
    Determine legal cards to play from a hand (Callbreak rules).
    - Leading: play anything.
    - Must follow led suit if possible.
    - If can't follow, must play spade (trump) if available.
    - If neither led suit nor spades, play anything.
    """
    if not played_cards or led_suit is None:
        return hand[:]

    follow = [c for c in hand if SUIT_MAP[c[-1]] == led_suit]
    if follow:
        return follow

    spades = [c for c in hand if SUIT_MAP[c[-1]] == Suit.SPADES]
    if spades:
        return spades

    return hand[:]


# -------------------------------------------------------------------------
# Winning card (Callbreak: Spades always trump)
# -------------------------------------------------------------------------
def get_winning_card(played_cards):
    """
    Determine the winning card string from a trick.
    Returns (winning_card_str, index_in_played_cards).
    """
    if not played_cards:
        return None, -1

    led_suit = SUIT_MAP[played_cards[0][-1]]
    best_card = played_cards[0]
    best_rank = int(played_cards[0][:-1])
    best_suit = led_suit
    best_idx = 0

    for i, card in enumerate(played_cards[1:], 1):
        card_suit = SUIT_MAP[card[-1]]
        card_rank = int(card[:-1])

        if card_suit == Suit.SPADES and best_suit != Suit.SPADES:
            best_card, best_rank, best_suit, best_idx = card, card_rank, card_suit, i
        elif card_suit == best_suit and card_rank > best_rank:
            best_card, best_rank, best_idx = card, card_rank, i

    return best_card, best_idx


# -------------------------------------------------------------------------
# Track void suits from trick play
# -------------------------------------------------------------------------
def track_void_suits(trick_cards, first_player_idx, void_tracker):
    """
    Track which suits each player is void in based on trick play.
    If a player plays off-suit from the led suit, they're void in it.
    void_tracker is a list of 4 lists (or sets).
    """
    if not trick_cards:
        return

    led_suit = SUIT_MAP[trick_cards[0][-1]]

    for i, card in enumerate(trick_cards):
        player_idx = (first_player_idx + i) % 4
        card_suit = SUIT_MAP[card[-1]]

        if card_suit != led_suit:
            if isinstance(void_tracker[player_idx], set):
                void_tracker[player_idx].add(led_suit)
            elif led_suit not in void_tracker[player_idx]:
                void_tracker[player_idx].append(led_suit)


# -------------------------------------------------------------------------
# Distribute hidden cards for determinization (robust version)
# -------------------------------------------------------------------------
def distribute_hidden_cards(unseen_cards, hands, needed, root_idx, void_tracker, max_retries=20):
    """
    Distribute unknown cards to opponents for MCTS determinization.
    Respects known void suits from the void_tracker.

    Args:
        unseen_cards: list of card strings not known to root player
        hands: list of 4 lists — only hands[root_idx] is pre-filled
        needed: list of 4 ints — how many cards each player needs
        root_idx: root player index (their hand is already known)
        void_tracker: list of 4 sets of suits each player is void in
        max_retries: number of shuffle-retry attempts for constraint satisfaction

    Modifies hands in-place.
    """
    total_needed = sum(needed)
    if total_needed == 0 or len(unseen_cards) < total_needed:
        # Fallback: give whatever we can
        if len(unseen_cards) > 0 and total_needed > 0:
            random.shuffle(unseen_cards)
            card_idx = 0
            for i in range(4):
                if i == root_idx or needed[i] == 0:
                    continue
                give = min(needed[i], len(unseen_cards) - card_idx)
                hands[i].extend(unseen_cards[card_idx:card_idx + give])
                card_idx += give
        return

    # Convert void_tracker to sets
    void_sets = []
    for vt in void_tracker:
        void_sets.append(set(vt) if not isinstance(vt, set) else vt)

    has_constraints = any(len(void_sets[i]) > 0 for i in range(4) if i != root_idx)

    for attempt in range(max_retries):
        random.shuffle(unseen_cards)

        if not has_constraints:
            card_idx = 0
            for i in range(4):
                if i == root_idx or needed[i] == 0:
                    continue
                hands[i] = unseen_cards[card_idx:card_idx + needed[i]]
                card_idx += needed[i]
            return

        # Try to satisfy void constraints
        temp_hands = [[] for _ in range(4)]
        remaining = list(unseen_cards)
        success = True

        opponent_order = [i for i in range(4) if i != root_idx and needed[i] > 0]
        opponent_order.sort(key=lambda i: -len(void_sets[i]))

        for p_idx in opponent_order:
            n = needed[p_idx]
            p_voids = void_sets[p_idx]
            valid = [c for c in remaining if SUIT_MAP[c[-1]] not in p_voids]

            if len(valid) < n:
                success = False
                break

            chosen = valid[:n]
            temp_hands[p_idx] = chosen
            for c in chosen:
                remaining.remove(c)

        if success:
            for i in range(4):
                if i == root_idx or needed[i] == 0:
                    continue
                hands[i] = temp_hands[i]
            return

    # Fallback: ignore void constraints
    random.shuffle(unseen_cards)
    card_idx = 0
    for i in range(4):
        if i == root_idx or needed[i] == 0:
            continue
        hands[i] = unseen_cards[card_idx:card_idx + needed[i]]
        card_idx += needed[i]
