# -----------
# MCTS Search for Callbreak — Flat IS-MCTS with fast rollout.
#
# Architecture:
#   - Flat 1-level tree (multi-armed bandit with UCB1 per determinization)
#   - Fast mutable rollout (lightweight heuristic, no BotLogic overhead)
#   - Information Set MCTS: aggregate stats across determinizations
#   - Correct opponent hand sizes based on game progress
# -----------

import math
import random
import time

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bid_logic import Suit, SUIT_MAP
from bot_logic import BotLogic, Player, GameHelper
from callbreak_mcts.mcts_state import CallbreakState
from callbreak_mcts.utils import get_legal_cards, distribute_hidden_cards

# -------------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------------
EXPLORATION_C = 1.41
HEURISTIC_PROB = 0.8   # 80% BotLogic, 20% random in rollout
_SUIT_ORDER = {"S": 0, "H": 1, "C": 2, "D": 3}

# Shared GameHelper (stateless, reused across all rollouts)
_game_helper = GameHelper()


# -------------------------------------------------------------------------
# Rollout using full BotLogic
# -------------------------------------------------------------------------
def _rollout(state, root_player, block_leader, cumulative_scores, human_index,
             current_round, total_rounds):
    """Rollout using BotLogic for card selection. Mutates state."""

    # Create lightweight Player + BotLogic for each player
    players = []
    bot_logics = {}
    for i in range(4):
        p = Player(i, f"R{i}")
        p.cards = state.hands[i][:]
        p.bid = state.bids[i]
        p.hands = state.tricks_won[i]
        players.append(p)

    for i in range(4):
        bot = BotLogic(players[i], _game_helper, players, skip_bid_logic=True)
        bot.on_deal_completed()
        bot_logics[i] = bot

    # We need to track play_turn for BotLogic events
    play_turn = 0

    # If we're mid-trick, we need to handle the partial trick first
    # cards_played has the cards already played in the current trick
    partial_cards = state.cards_played[:]
    trick_starter = state.trick_starter

    # Fire on_throw_turn_started for the current trick
    for idx in bot_logics:
        bot_logics[idx].on_throw_turn_started(
            1, play_turn, 0, players[trick_starter]
        )

    # Replay partial cards through BotLogic events (they're already in state)
    throw_turn_offset = len(partial_cards)
    for tt, card in enumerate(partial_cards):
        thrown_by_idx = (trick_starter + tt) % 4
        for idx in bot_logics:
            bot_logics[idx].on_throw_card_selected(
                card, 1, play_turn, tt, players[thrown_by_idx]
            )

    while not state.is_round_over():
        legal = state.get_legal_moves()
        if not legal:
            break

        cur = state.current_turn
        throw_turn = len(state.cards_played)
        trick_starter = state.trick_starter

        # Set legal cards on player for BotLogic
        players[cur].legal_cards = legal

        if len(legal) == 1:
            card = legal[0]
        elif random.random() < HEURISTIC_PROB:
            try:
                card = bot_logics[cur].select_throw_card(
                    throw_turn, players[0], state.cards_played[:]
                )
                # Validate the card is legal
                if card not in legal:
                    card = random.choice(legal)
            except Exception:
                card = random.choice(legal)
        else:
            card = random.choice(legal)

        # Track if this will complete a trick (4th card)
        will_complete_trick = (len(state.cards_played) == 3)
        cards_before = state.cards_played[:] + [card]

        # Play the card
        state.play_card_inplace(card)
        players[cur].cards.remove(card)

        # Notify all BotLogics about the card
        for idx in bot_logics:
            bot_logics[idx].on_throw_card_selected(
                card, 1, play_turn, throw_turn, players[cur]
            )

        # If trick completed
        if will_complete_trick:
            # Determine winner from the played cards
            last_player_idx = (trick_starter + 3) % 4

            # Update player trick counts from state
            for i in range(4):
                players[i].hands = state.tricks_won[i]

            for idx in bot_logics:
                bot_logics[idx].on_throw_turn_completed(
                    3, players[last_player_idx], cards_before
                )

            play_turn += 1

            # Fire on_throw_turn_started for next trick if game not over
            if not state.is_round_over():
                new_starter = state.trick_starter
                for idx in bot_logics:
                    bot_logics[idx].on_throw_turn_started(
                        1, play_turn, 0, players[new_starter]
                    )

    return state.get_reward(
        root_player,
        block_leader,
        cumulative_scores,
        human_index,
        current_round=current_round,
        total_rounds=total_rounds,
    )


# -------------------------------------------------------------------------
# MCTS Search — flat IS-MCTS with determinization
# -------------------------------------------------------------------------
def mcts_search(
    original_deck,
    known_hand,
    bids,
    tricks_won,
    current_turn,
    cards_played,
    trick_starter,
    discard_pile,
    led_suit,
    void_tracker,
    player_index=0,
    iterations=200,
    simulations_per_det=10,
    time_limit_ms=None,
    block_leader=False,
    cumulative_scores=None,
    human_index=-1,
    current_round=1,
    total_rounds=5,
):
    """
    Run flat IS-MCTS for the best card to play.

    Each determinization samples opponent hands, then runs a flat UCB1 bandit
    over the root player's legal actions with rollout evaluation.

    Returns: (best_card_str, action_stats_dict)
    """
    legal = get_legal_cards(known_hand, cards_played, led_suit)
    if len(legal) == 1:
        return legal[0], {}

    action_stats = {card: {"v": 0, "w": 0.0} for card in legal}

    # Unseen cards: not in discard, not in our hand
    known_set = set(discard_pile)
    known_set.update(known_hand)
    known_set.update(cards_played)
    unseen_base = [c for c in original_deck if c not in known_set]

    # Void tracker as sets
    void_sets = []
    for vt in void_tracker:
        void_sets.append(set(vt) if not isinstance(vt, set) else vt)

    # Compute correct hand sizes for each opponent
    complete_tricks = sum(tricks_won)
    opponent_card_counts = {}
    for i in range(4):
        if i == player_index:
            continue
        played_in_trick = 0
        for j in range(len(cards_played)):
            if (trick_starter + j) % 4 == i:
                played_in_trick = 1
                break
        opponent_card_counts[i] = 13 - complete_tricks - played_in_trick

    total_cards_played = len(discard_pile) + len(cards_played)

    # Run determinizations
    if time_limit_ms is not None and time_limit_ms > 0:
        deadline = time.time() + time_limit_ms / 1000.0
        while time.time() < deadline:
            _run_one_det(
                unseen_base, known_hand, bids, tricks_won,
                current_turn, cards_played, trick_starter,
                led_suit, void_sets, player_index, legal,
                simulations_per_det, action_stats,
                block_leader, cumulative_scores,
                total_cards_played, opponent_card_counts,
                human_index,
                current_round,
                total_rounds,
            )
    else:
        for _ in range(iterations):
            _run_one_det(
                unseen_base, known_hand, bids, tricks_won,
                current_turn, cards_played, trick_starter,
                led_suit, void_sets, player_index, legal,
                simulations_per_det, action_stats,
                block_leader, cumulative_scores,
                total_cards_played, opponent_card_counts,
                human_index,
                current_round,
                total_rounds,
            )

    # Compute averages and pick best
    for k in action_stats:
        v = action_stats[k]["v"]
        action_stats[k]["avg"] = action_stats[k]["w"] / v if v > 0 else 0.0

    best = max(action_stats, key=lambda k: (action_stats[k]["v"], action_stats[k]["avg"]))
    return best, action_stats


def _run_one_det(
    unseen_base, known_hand, bids, tricks_won,
    current_turn, cards_played, trick_starter,
    led_suit, void_sets, player_index, legal,
    sims_per_det, action_stats,
    block_leader, cumulative_scores,
    total_cards_played, opponent_card_counts,
    human_index,
    current_round,
    total_rounds,
):
    """One determinization: sample opponent hands, run flat UCB1 bandit."""

    # Build hands with correct sizes
    hands = [None] * 4
    hands[player_index] = known_hand[:]

    # Prepare opponent hands (empty, to be filled)
    needed = [0] * 4
    for i in range(4):
        if i == player_index:
            hands[i] = known_hand[:]
        else:
            hands[i] = []
            needed[i] = opponent_card_counts[i]

    # Distribute unseen cards to opponents
    unseen = unseen_base[:]
    distribute_hidden_cards(unseen, hands, needed, player_index, void_sets)

    # Sort hands
    for i in range(4):
        hands[i].sort(key=lambda c: (_SUIT_ORDER.get(c[-1], 4), -int(c[:-1])))

    # Create base state for this determinization
    base_state = CallbreakState(
        hands=hands,
        bids=bids,
        tricks_won=tricks_won[:],
        current_turn=current_turn,
        cards_played=cards_played[:],
        trick_starter=trick_starter,
        led_suit=led_suit,
        total_cards_played=total_cards_played,
    )

    # Per-determinization local stats for UCB1
    local_v = {a: 0 for a in legal}
    local_w = {a: 0.0 for a in legal}

    for _ in range(sims_per_det):
        # UCB1 action selection
        untried = [a for a in legal if local_v[a] == 0]
        if untried:
            action = random.choice(untried)
        else:
            total = sum(local_v[a] for a in legal)
            log_total = math.log(total) if total > 0 else 0
            action = max(legal, key=lambda a: (
                local_w[a] / local_v[a]
                + EXPLORATION_C * math.sqrt(log_total / local_v[a])
            ))

        # Rollout: copy state, play action, simulate to end
        rollout_state = base_state.copy()
        rollout_state.play_card_inplace(action)
        reward = _rollout(
            rollout_state,
            player_index,
            block_leader,
            cumulative_scores,
            human_index,
            current_round,
            total_rounds,
        )

        # Update local stats
        local_v[action] += 1
        local_w[action] += reward

    # Aggregate into global action_stats
    for a in legal:
        action_stats[a]["v"] += local_v[a]
        action_stats[a]["w"] += local_w[a]
