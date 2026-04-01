# -----------
# Minimal mutable game state for fast Callbreak MCTS rollout.
# play_card_inplace() mutates state (fast). copy() for rollout cloning.
# -----------

import math
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bid_logic import Suit, SUIT_MAP


def _smooth_pairwise_utility(my_value, opp_value, scale):
    return math.tanh((my_value - opp_value) / max(1.0, scale))


def _placement_signal_for_rank(projected_rank):
    if projected_rank <= 1:
        return 1.0
    if projected_rank == 2:
        return 0.12
    if projected_rank == 3:
        return -0.45
    return -0.90


def _get_winning_index(cards_played):
    """Return index (0-3) of the winning card in a completed trick."""
    led_suit = SUIT_MAP[cards_played[0][-1]]
    best_rank = int(cards_played[0][:-1])
    best_suit = led_suit
    best_idx = 0
    for i in range(1, len(cards_played)):
        c = cards_played[i]
        cs = SUIT_MAP[c[-1]]
        cr = int(c[:-1])
        if cs == Suit.SPADES and best_suit != Suit.SPADES:
            best_rank, best_suit, best_idx = cr, cs, i
        elif cs == best_suit and cr > best_rank:
            best_rank, best_idx = cr, i
    return best_idx


class CallbreakState:
    """Mutable game state for one round of Callbreak (13 tricks)."""

    __slots__ = (
        "hands", "bids", "tricks_won", "current_turn",
        "cards_played", "trick_starter", "led_suit",
        "total_cards_played",
    )

    def __init__(self, hands, bids, tricks_won, current_turn,
                 cards_played, trick_starter, led_suit, total_cards_played):
        self.hands = hands
        self.bids = bids
        self.tricks_won = tricks_won
        self.current_turn = current_turn
        self.cards_played = cards_played
        self.trick_starter = trick_starter
        self.led_suit = led_suit
        self.total_cards_played = total_cards_played

    def copy(self):
        """Fast copy for rollout cloning."""
        new = CallbreakState.__new__(CallbreakState)
        new.hands = [h[:] for h in self.hands]
        new.bids = self.bids          # shared ref, never modified
        new.tricks_won = self.tricks_won[:]
        new.current_turn = self.current_turn
        new.cards_played = self.cards_played[:]
        new.trick_starter = self.trick_starter
        new.led_suit = self.led_suit
        new.total_cards_played = self.total_cards_played
        return new

    def is_round_over(self):
        return self.total_cards_played >= 52

    def get_legal_moves(self):
        hand = self.hands[self.current_turn]
        if not self.cards_played or self.led_suit is None:
            return hand[:]
        follow = [c for c in hand if SUIT_MAP[c[-1]] == self.led_suit]
        if follow:
            return follow
        spades = [c for c in hand if SUIT_MAP[c[-1]] == Suit.SPADES]
        if spades:
            return spades
        return hand[:]

    def play_card_inplace(self, card):
        """Play a card, mutating state in-place (fast)."""
        cur = self.current_turn
        self.hands[cur].remove(card)
        self.cards_played.append(card)
        self.total_cards_played += 1

        if len(self.cards_played) == 1:
            self.led_suit = SUIT_MAP[card[-1]]

        self.current_turn = (cur + 1) % 4

        if len(self.cards_played) == 4:
            win_off = _get_winning_index(self.cards_played)
            winner = (self.trick_starter + win_off) % 4
            self.tricks_won[winner] += 1
            self.cards_played = []
            self.led_suit = None
            self.current_turn = winner
            self.trick_starter = winner

    def get_reward(
        self,
        player_idx,
        block_leader=False,
        cumulative_scores=None,
        human_index=-1,
        current_round=1,
        total_rounds=5,
    ):
        normalized_total_rounds = max(1, total_rounds)
        normalized_current_round = min(max(1, current_round), normalized_total_rounds)
        future_rounds = normalized_total_rounds - normalized_current_round
        urgency = (
            float(normalized_current_round - 1) / float(normalized_total_rounds - 1)
            if normalized_total_rounds > 1 else 1.0
        )
        late_pressure = 0.5 * urgency + 0.5 * urgency * urgency
        pairwise_scale = 6.0 + 3.0 * float(future_rounds)

        round_scores = []
        final_scores = []
        base_scores = cumulative_scores if cumulative_scores is not None else [0.0] * 4
        for idx in range(4):
            tricks = self.tricks_won[idx]
            bid = self.bids[idx]
            round_score = float(-bid if tricks < bid else bid + 0.1 * (tricks - bid))
            round_scores.append(round_score)
            final_scores.append(base_scores[idx] + round_score)

        my_score = round_scores[player_idx]
        my_final = final_scores[player_idx]
        opp_finals = [final_scores[i] for i in range(4) if i != player_idx]
        opp_rounds = [round_scores[i] for i in range(4) if i != player_idx]

        pairwise_utility = sum(
            _smooth_pairwise_utility(my_final, opp_final, pairwise_scale)
            for opp_final in opp_finals
        )
        top_opp_final = max(opp_finals)
        avg_opp_final = sum(opp_finals) / 3.0
        avg_opp_round = sum(opp_rounds) / 3.0
        projected_rank = 1 + sum(
            1 for opp_final in opp_finals
            if opp_final > my_final + 1e-9
        )

        if my_final >= top_opp_final - 1e-9:
            lead_gap = my_final - top_opp_final
            first_place_signal = math.tanh(
                lead_gap / (4.5 + 1.5 * float(future_rounds))
            )
        else:
            rounds_to_recover = future_rounds if future_rounds > 0 else 1
            required_swing = (top_opp_final - my_final) / float(rounds_to_recover)
            first_place_signal = -math.tanh(required_swing / 3.0)

        score_margin_signal = math.tanh((my_final - avg_opp_final) / (pairwise_scale + 2.0))
        round_margin_signal = math.tanh((my_score - avg_opp_round) / 4.0)
        self_round_signal = math.tanh(my_score / 4.0)
        winner_take_signal = 1.0 if projected_rank == 1 else -0.55

        reward = (
            (1.35 + 0.75 * late_pressure) * pairwise_utility
            + (0.60 + 1.25 * late_pressure) * first_place_signal
            + (0.10 + 0.95 * late_pressure) * _placement_signal_for_rank(projected_rank)
            + (0.05 + 1.45 * late_pressure) * winner_take_signal
            + (0.18 + 0.42 * late_pressure) * score_margin_signal
            + (0.28 - 0.08 * late_pressure) * round_margin_signal
            + (0.18 + 0.34 * late_pressure) * self_round_signal
        )

        if block_leader and cumulative_scores:
            leader_idx = max(
                (i for i in range(4) if i != player_idx),
                key=lambda i: cumulative_scores[i]
            )
            reward += (0.18 + 0.55 * late_pressure) * _smooth_pairwise_utility(
                my_final, final_scores[leader_idx], pairwise_scale
            )

        if cumulative_scores and future_rounds == 0:
            my_bid_floor = cumulative_scores[player_idx] + float(self.bids[player_idx])
            max_opp_bid_floor = max(
                cumulative_scores[i] + float(self.bids[i])
                for i in range(4) if i != player_idx
            )
            my_made_bid = self.tricks_won[player_idx] >= self.bids[player_idx]

            for opp in range(4):
                if opp == player_idx:
                    continue
                opp_bid_floor = cumulative_scores[opp] + float(self.bids[opp])
                if opp_bid_floor >= my_bid_floor - 0.25:
                    adjusted_gap = opp_bid_floor - my_bid_floor + 0.25
                    threat_signal = math.tanh(adjusted_gap / 2.5)
                    opp_made_bid = self.tricks_won[opp] >= self.bids[opp]
                    reward += 1.10 * (-threat_signal if opp_made_bid else 0.75 * threat_signal)

            if my_bid_floor > max_opp_bid_floor + 1e-9:
                safety_signal = math.tanh((my_bid_floor - max_opp_bid_floor) / 2.5)
                reward += 0.85 * (
                    safety_signal if my_made_bid else -(1.15 * safety_signal + 0.20)
                )

        return reward
