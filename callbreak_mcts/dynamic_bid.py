import math
import random

from bid_logic import SUIT_MAP
from bot_logic import BotLogic, GameHelper, Player
from callbreak_mcts.mcts_state import CallbreakState
from callbreak_mcts.utils import get_legal_cards, get_winning_card, track_void_suits

NUM_PLAYERS = 4
CARDS_PER_HAND = 13
FULL_DECK = [f"{rank}{suit}" for suit in ["D", "C", "H", "S"] for rank in range(2, 15)]


def estimate_mcts_bid(
    *,
    player_index,
    known_hand,
    known_bids,
    dealer_index,
    cumulative_scores,
    current_round,
    total_rounds,
    mcts_card_picker,
    base_iterations,
    base_simulations_per_det,
    time_limit_ms=None,
    block_leader=False,
    rule_card_picker=None,
    mcts_player_indices=None,
    player_in_game_ids=None,
):
    """Estimate a bid for one MCTS seat without changing the play architecture."""
    anchor_bid = _rule_bid_for_hand(known_hand, player_index)
    eval_iterations = _bid_iteration_budget(base_iterations)
    pilot_iterations = max(16, eval_iterations // 2)
    opponent_iterations = _opponent_bid_iteration_budget(eval_iterations)
    eval_world_count = 8
    pilot_world_count = 4
    if mcts_player_indices is None:
        mcts_player_indices = {player_index}
    else:
        mcts_player_indices = set(mcts_player_indices)

    sampled_worlds = [
        _sample_hidden_world(player_index, known_hand, known_bids)
        for _ in range(eval_world_count)
    ]

    pilot_tricks = []
    for sampled_hands in sampled_worlds[:pilot_world_count]:
        outcome = _simulate_round(
            sampled_hands=sampled_hands,
            player_index=player_index,
            bid_for_player=anchor_bid,
            known_bids=known_bids,
            dealer_index=dealer_index,
            cumulative_scores=cumulative_scores,
            current_round=current_round,
            total_rounds=total_rounds,
            mcts_card_picker=mcts_card_picker,
            rule_card_picker=rule_card_picker,
            mcts_player_indices=mcts_player_indices,
            iterations=pilot_iterations,
            simulations_per_det=max(1, min(2, base_simulations_per_det)),
            opponent_iterations=max(12, opponent_iterations // 2),
            opponent_simulations_per_det=1,
            time_limit_ms=time_limit_ms,
            block_leader=block_leader,
            player_in_game_ids=player_in_game_ids,
        )
        pilot_tricks.append(outcome["tricks"])

    candidate_bids = _candidate_bids(
        pilot_tricks=pilot_tricks,
        anchor_bid=anchor_bid,
        cumulative_scores=cumulative_scores,
        player_index=player_index,
        current_round=current_round,
        total_rounds=total_rounds,
    )

    evaluations = []
    for candidate_bid in candidate_bids:
        tricks = []
        round_scores = []
        win_shares = []
        margins = []
        rewards = []

        for sampled_hands in sampled_worlds:
            outcome = _simulate_round(
                sampled_hands=sampled_hands,
                player_index=player_index,
                bid_for_player=candidate_bid,
                known_bids=known_bids,
                dealer_index=dealer_index,
                cumulative_scores=cumulative_scores,
                current_round=current_round,
                total_rounds=total_rounds,
                mcts_card_picker=mcts_card_picker,
                rule_card_picker=rule_card_picker,
                mcts_player_indices=mcts_player_indices,
                iterations=eval_iterations,
                simulations_per_det=max(1, min(2, base_simulations_per_det)),
                opponent_iterations=opponent_iterations,
                opponent_simulations_per_det=1,
                time_limit_ms=time_limit_ms,
                block_leader=block_leader,
                player_in_game_ids=player_in_game_ids,
            )
            tricks.append(outcome["tricks"])
            round_scores.append(outcome["round_score"])
            win_shares.append(outcome["win_share"])
            margins.append(outcome["margin"])
            rewards.append(outcome["reward"])

        mean_tricks = sum(tricks) / len(tricks)
        make_rate = sum(1 for value in tricks if value >= candidate_bid) / len(tricks)
        trick_stddev = _stddev(tricks, mean_tricks)
        expected_score = sum(round_scores) / len(round_scores)
        win_share = sum(win_shares) / len(win_shares)
        expected_margin = sum(margins) / len(margins)
        expected_reward = sum(rewards) / len(rewards)

        evaluations.append({
            "bid": candidate_bid,
            "mean_tricks": mean_tricks,
            "make_rate": make_rate,
            "expected_score": expected_score,
            "win_share": win_share,
            "expected_margin": expected_margin,
            "trick_stddev": trick_stddev,
            "expected_reward": expected_reward,
        })

    if not evaluations:
        return max(1, min(CARDS_PER_HAND, anchor_bid))

    selected = _select_evaluation(
        evaluations=evaluations,
        cumulative_scores=cumulative_scores,
        player_index=player_index,
        current_round=current_round,
        total_rounds=total_rounds,
    )
    return int(max(1, min(CARDS_PER_HAND, selected["bid"])))


def _bid_iteration_budget(base_iterations):
    if not base_iterations or base_iterations <= 0:
        return 48
    return max(24, min(96, base_iterations // 12))


def _opponent_bid_iteration_budget(eval_iterations):
    return max(12, min(48, eval_iterations // 3))


def _rule_bid_for_hand(hand, player_index):
    game_helper = GameHelper()
    players = []
    for seat in range(NUM_PLAYERS):
        player = Player(seat, f"P{seat}")
        player.cards = hand[:] if seat == player_index else []
        player.bid = 0
        player.hands = 0
        players.append(player)

    bot = BotLogic(players[player_index], game_helper, players)
    bot.on_deal_completed()
    return max(1, min(CARDS_PER_HAND, bot.compute_bid()))


def _card_strength(card):
    rank = int(card[:-1])
    strength = rank - 2
    if card[-1] == "S":
        strength += 4.0
    if rank >= 11:
        strength += 0.5 * (rank - 10)
    return strength


def _hand_strength(hand):
    spades = [int(card[:-1]) for card in hand if card[-1] == "S"]
    strength = sum(_card_strength(card) for card in hand)
    strength += max(0, len(spades) - 3) * 1.4
    strength += sum(1.0 for rank in spades if rank >= 11)
    return strength


def _sample_hidden_world(player_index, known_hand, known_bids):
    hidden_cards = [card for card in FULL_DECK if card not in set(known_hand)]
    opponent_indices = [seat for seat in range(NUM_PLAYERS) if seat != player_index]

    best_world = None
    best_score = None
    for _ in range(5):
        shuffled = hidden_cards[:]
        random.shuffle(shuffled)

        sampled_hands = [[] for _ in range(NUM_PLAYERS)]
        sampled_hands[player_index] = known_hand[:]

        offset = 0
        for seat in opponent_indices:
            sampled_hands[seat] = shuffled[offset:offset + CARDS_PER_HAND]
            offset += CARDS_PER_HAND

        compatibility = _bid_compatibility(sampled_hands, known_bids, player_index)
        if best_score is None or compatibility > best_score:
            best_score = compatibility
            best_world = sampled_hands

    return best_world


def _bid_compatibility(sampled_hands, known_bids, player_index):
    known_players = [
        seat for seat in range(NUM_PLAYERS)
        if seat != player_index and known_bids[seat] > 0
    ]
    if not known_players:
        return 0.0

    strengths = {seat: _hand_strength(sampled_hands[seat]) for seat in known_players}
    avg_strength = sum(strengths.values()) / len(strengths)

    score = 0.0
    for seat in known_players:
        centered_bid = known_bids[seat] - 4.0
        score += centered_bid * ((strengths[seat] - avg_strength) / 6.0)

    for left in known_players:
        for right in known_players:
            if left >= right or known_bids[left] == known_bids[right]:
                continue
            same_order = (known_bids[left] - known_bids[right]) * (strengths[left] - strengths[right])
            score += 1.25 if same_order > 0 else -1.25

    return score


def _candidate_bids(pilot_tricks, anchor_bid, cumulative_scores, player_index, current_round, total_rounds):
    if not pilot_tricks:
        return [max(1, min(CARDS_PER_HAND, anchor_bid))]

    sorted_tricks = sorted(pilot_tricks)
    mean_tricks = sum(pilot_tricks) / len(pilot_tricks)
    low = _quantile(sorted_tricks, 0.30)
    mid = _quantile(sorted_tricks, 0.50)
    high = _quantile(sorted_tricks, 0.70)

    leader_score = max(cumulative_scores[seat] for seat in range(NUM_PLAYERS) if seat != player_index)
    gap_to_leader = cumulative_scores[player_index] - leader_score
    final_round = current_round >= total_rounds
    rounds_left = max(0, total_rounds - current_round)

    candidates = {
        max(1, min(CARDS_PER_HAND, anchor_bid)),
        max(1, min(CARDS_PER_HAND, int(round(low)))),
        max(1, min(CARDS_PER_HAND, int(round(mid)))),
        max(1, min(CARDS_PER_HAND, int(round(mean_tricks)))),
        max(1, min(CARDS_PER_HAND, int(round(high)))),
    }

    if gap_to_leader > 0:
        candidates.add(max(1, min(CARDS_PER_HAND, int(math.floor(mean_tricks - 1.0)))))
    elif gap_to_leader < 0 and rounds_left <= 1:
        candidates.add(max(1, min(CARDS_PER_HAND, int(math.ceil(mean_tricks + 1.0)))))
        if final_round:
            candidates.add(max(1, min(CARDS_PER_HAND, int(math.ceil(high + 1.0)))))

    ordered = sorted(candidates)
    if len(ordered) <= 5:
        return ordered

    focus = {ordered[0], ordered[-1]}
    focus.add(min(ordered, key=lambda bid: abs(bid - mean_tricks)))
    focus.add(min(ordered, key=lambda bid: abs(bid - low)))
    focus.add(min(ordered, key=lambda bid: abs(bid - high)))
    return sorted(focus)


def _simulate_round(
    *,
    sampled_hands,
    player_index,
    bid_for_player,
    known_bids,
    dealer_index,
    cumulative_scores,
    current_round,
    total_rounds,
    mcts_card_picker,
    rule_card_picker,
    mcts_player_indices,
    iterations,
    simulations_per_det,
    opponent_iterations,
    opponent_simulations_per_det,
    time_limit_ms,
    block_leader,
    player_in_game_ids,
):
    game_helper = GameHelper()
    players = []
    bot_logics = {}

    for seat in range(NUM_PLAYERS):
        player = Player(seat, f"P{seat}")
        player.cards = sampled_hands[seat][:]
        player.bid = 0
        player.hands = 0
        players.append(player)

    for seat in range(NUM_PLAYERS):
        bot = BotLogic(players[seat], game_helper, players)
        bot.on_deal_completed()
        bot_logics[seat] = bot

    dealer = players[dealer_index]
    full_bids = known_bids[:]
    full_bids[player_index] = bid_for_player

    bidding_order = [game_helper.get_bid_player(dealer, players, turn).in_game_id for turn in range(NUM_PLAYERS)]
    seen_current = False
    for seat in bidding_order:
        if seat == player_index:
            seen_current = True
            continue
        if not seen_current:
            continue
        if full_bids[seat] <= 0:
            full_bids[seat] = max(1, min(CARDS_PER_HAND, bot_logics[seat].compute_bid()))

    for seat in range(NUM_PLAYERS):
        if full_bids[seat] <= 0:
            full_bids[seat] = max(1, min(CARDS_PER_HAND, bot_logics[seat].compute_bid()))
        players[seat].bid = full_bids[seat]

    tricks_won = [0] * NUM_PLAYERS
    discard_pile = []
    discard_starters = []
    void_tracker = [set() for _ in range(NUM_PLAYERS)]
    turn_starter_idx = bidding_order[0]
    stable_player_ids = player_in_game_ids or [player.in_game_id for player in players]

    for play_turn in range(CARDS_PER_HAND):
        played_cards = []
        starter_idx = turn_starter_idx

        for seat in bot_logics:
            bot_logics[seat].on_throw_turn_started(current_round, play_turn, 0, players[starter_idx])

        for throw_turn in range(NUM_PLAYERS):
            current_idx = (starter_idx + throw_turn) % NUM_PLAYERS
            current_player = players[current_idx]
            led_suit = SUIT_MAP[played_cards[0][-1]] if played_cards else None
            legal = get_legal_cards(current_player.cards, played_cards, led_suit)
            current_player.legal_cards = legal

            if len(legal) == 1:
                selected = legal[0]
            elif current_idx == player_index:
                selected = mcts_card_picker(
                    original_deck=FULL_DECK,
                    known_hand=current_player.cards[:],
                    bids=full_bids[:],
                    tricks_won=tricks_won[:],
                    current_turn=current_idx,
                    cards_played=played_cards[:],
                    trick_starter=starter_idx,
                    discard_pile=discard_pile[:],
                    led_suit=led_suit,
                    void_tracker=[set(v) for v in void_tracker],
                    dealer_index=dealer_index,
                    player_index=current_idx,
                    iterations=iterations,
                    simulations_per_det=simulations_per_det,
                    time_limit_ms=time_limit_ms,
                    block_leader=block_leader,
                    cumulative_scores=cumulative_scores[:],
                    current_round=current_round,
                    total_rounds=total_rounds,
                    discard_starters=discard_starters[:],
                    player_in_game_ids=stable_player_ids,
                )
            elif current_idx in mcts_player_indices:
                selected = mcts_card_picker(
                    original_deck=FULL_DECK,
                    known_hand=current_player.cards[:],
                    bids=full_bids[:],
                    tricks_won=tricks_won[:],
                    current_turn=current_idx,
                    cards_played=played_cards[:],
                    trick_starter=starter_idx,
                    discard_pile=discard_pile[:],
                    led_suit=led_suit,
                    void_tracker=[set(v) for v in void_tracker],
                    dealer_index=dealer_index,
                    player_index=current_idx,
                    iterations=opponent_iterations,
                    simulations_per_det=opponent_simulations_per_det,
                    time_limit_ms=time_limit_ms,
                    block_leader=block_leader,
                    cumulative_scores=cumulative_scores[:],
                    current_round=current_round,
                    total_rounds=total_rounds,
                    discard_starters=discard_starters[:],
                    player_in_game_ids=stable_player_ids,
                )
            elif rule_card_picker is not None:
                selected = rule_card_picker(
                    current_idx=current_idx,
                    current_player=current_player,
                    dealer=dealer,
                    throw_turn=throw_turn,
                    bot_logics=bot_logics,
                    bids=full_bids[:],
                    tricks_won=tricks_won[:],
                    played_cards=played_cards[:],
                    starter_idx=starter_idx,
                    discard_pile=discard_pile[:],
                    led_suit=led_suit,
                    dealer_index=dealer_index,
                    discard_starters=discard_starters[:],
                )
            else:
                selected = bot_logics[current_idx].select_throw_card(throw_turn, dealer, played_cards[:])

            if selected not in legal:
                selected = random.choice(legal)

            played_cards.append(selected)
            current_player.cards.remove(selected)
            discard_pile.append(selected)

            for seat in bot_logics:
                bot_logics[seat].on_throw_card_selected(
                    selected, current_round, play_turn, throw_turn, current_player
                )

        _, winner_offset = get_winning_card(played_cards)
        winner_idx = (starter_idx + winner_offset) % NUM_PLAYERS
        tricks_won[winner_idx] += 1
        players[winner_idx].hands += 1
        discard_starters.append(starter_idx)

        track_void_suits(played_cards, starter_idx, void_tracker)

        last_player_idx = (starter_idx + 3) % NUM_PLAYERS
        for seat in bot_logics:
            bot_logics[seat].on_throw_turn_completed(3, players[last_player_idx], played_cards)

        turn_starter_idx = winner_idx

    round_scores = [_round_score(full_bids[seat], tricks_won[seat]) for seat in range(NUM_PLAYERS)]
    final_scores = [cumulative_scores[seat] + round_scores[seat] for seat in range(NUM_PLAYERS)]
    top_score = max(final_scores)
    winners = [seat for seat, score in enumerate(final_scores) if abs(score - top_score) < 1e-9]
    win_share = (1.0 / len(winners)) if player_index in winners else 0.0
    leader_after_round = max(final_scores[seat] for seat in range(NUM_PLAYERS) if seat != player_index)
    reward_state = CallbreakState(
        hands=[[] for _ in range(NUM_PLAYERS)],
        bids=full_bids[:],
        tricks_won=tricks_won[:],
        current_turn=player_index,
        cards_played=[],
        trick_starter=turn_starter_idx,
        led_suit=None,
        total_cards_played=52,
    )
    strategic_reward = reward_state.get_reward(
        player_index,
        block_leader=block_leader,
        cumulative_scores=cumulative_scores[:],
        current_round=current_round,
        total_rounds=total_rounds,
    )

    return {
        "tricks": tricks_won[player_index],
        "round_score": round_scores[player_index],
        "win_share": win_share,
        "margin": final_scores[player_index] - leader_after_round,
        "reward": strategic_reward,
    }


def _round_score(bid, tricks_won):
    if tricks_won >= bid:
        return bid + 0.1 * (tricks_won - bid)
    return -bid


def _quantile(sorted_values, quantile):
    if not sorted_values:
        return 1.0
    index = int(round((len(sorted_values) - 1) * quantile))
    index = max(0, min(len(sorted_values) - 1, index))
    return float(sorted_values[index])


def _stddev(values, mean_value):
    if len(values) <= 1:
        return 0.0
    variance = sum((value - mean_value) ** 2 for value in values) / len(values)
    return math.sqrt(variance)


def _bid_profile(cumulative_scores, player_index, current_round, total_rounds):
    leader_score = max(cumulative_scores[seat] for seat in range(NUM_PLAYERS) if seat != player_index)
    gap_to_leader = cumulative_scores[player_index] - leader_score
    rounds_left = max(0, total_rounds - current_round)

    if gap_to_leader >= 3.0:
        return {"mode": "protect", "min_make_rate": 0.74}

    if gap_to_leader >= 0.0:
        return {"mode": "balanced", "min_make_rate": 0.74 if rounds_left <= 1 else 0.70}

    if rounds_left == 0:
        if gap_to_leader <= -5.0:
            return {"mode": "desperate", "min_make_rate": 0.56}
        return {"mode": "chase", "min_make_rate": 0.62}

    if rounds_left == 1 and gap_to_leader <= -4.0:
        return {"mode": "chase", "min_make_rate": 0.62}

    return {"mode": "balanced", "min_make_rate": 0.70}


def _select_evaluation(evaluations, cumulative_scores, player_index, current_round, total_rounds):
    profile = _bid_profile(cumulative_scores, player_index, current_round, total_rounds)
    qualified = [
        item for item in evaluations
        if item["make_rate"] >= profile["min_make_rate"]
    ]

    if profile["mode"] == "protect":
        pool = qualified or evaluations
        best_win_share = max(item["win_share"] for item in pool)
        safe_pool = [
            item for item in pool
            if item["win_share"] >= best_win_share - 0.03
        ]
        return max(
            safe_pool,
            key=lambda item: (
                item["make_rate"],
                item["expected_reward"],
                -item["trick_stddev"],
                -item["bid"],
            ),
        )

    if profile["mode"] == "balanced":
        pool = qualified or evaluations
        return max(
            pool,
            key=lambda item: (
                item["expected_reward"],
                item["make_rate"],
                -item["trick_stddev"],
                -item["bid"],
            ),
        )

    if profile["mode"] == "chase":
        pool = qualified or evaluations
        return max(
            pool,
            key=lambda item: (
                item["win_share"],
                item["expected_reward"],
                item["make_rate"],
                item["bid"],
            ),
        )

    return max(
        evaluations,
        key=lambda item: (
            item["win_share"],
            item["expected_reward"],
            item["make_rate"],
            item["bid"],
        ),
    )
