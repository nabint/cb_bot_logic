import math
from enum import IntEnum


class Suit(IntEnum):
    DIAMONDS = 1
    CLUBS = 2
    HEARTS = 3
    SPADES = 4


class Strategy(IntEnum):
    PREPARE_FOR_CUT = 0
    COMPETE_SPADES = 1
    BRING_DOWN_ACE = 2
    BRING_DOWN_KING = 3


class GameMode(IntEnum):
    STANDARD = 1
    QUICK = 2
    EIGHT_BID_CALL = 3
    EIGHT_BID_BREAK = 4
    TRAINING = 5
    TUTORIAL = 6


SUIT_MAP = {
    "D": Suit.DIAMONDS,
    "C": Suit.CLUBS,
    "H": Suit.HEARTS,
    "S": Suit.SPADES,
}

SUIT_LETTER = {
    Suit.DIAMONDS: "D",
    Suit.CLUBS: "C",
    Suit.HEARTS: "H",
    Suit.SPADES: "S",
}

SPADE_PROBS = {
    14: 1.00,
    13: (0.832804 + 0.464285) / 2.0,
    12: (0.654330 + 0.428571) / 2.0,
    11: (0.485500 + 0.392857) / 2.0,
    10: 0.281416,
    9: 0.225944,
    8: 0.232700,
    7: 0.177419,
    6: 0.126819,
    5: 0.091100,
    4: 0.070872,
    3: 0.050906,
    2: 0.030173,
}

# Outer key = My total cards in a suit
# Inner key = All opponents should have at least this many cards
PROB_DIST = {
    0: {1: 0.995883, 2: 0.949609, 3: 0.727636, 4: 0.246271, 5: 0},
    1: {1: 0.992759, 2: 0.915651, 3: 0.603479, 4: 0.093717, 5: 0},
    2: {1: 0.986198, 2: 0.862628, 3: 0.450359, 4: 0},
    3: {1: 0.974804, 2: 0.783795, 3: 0.27687, 4: 0},
    4: {1: 0.956055, 2: 0.672427, 3: 0.110936, 4: 0},
    5: {1: 0.924156, 2: 0.524086, 3: 0},
    6: {1: 0.872706, 2: 0.339533, 3: 0},
    7: {1: 0.790307, 2: 0.145071, 3: 0},
    8: {1: 0.668897, 2: 0},
    9: {1: 0.504717, 2: 0},
    10: {1: 0.2, 2: 0},
    11: {1: 0, 2: 0},
    12: {1: 0, 2: 0},
    13: {1: 0, 2: 0},
}


class BidLogic:
    """
    Computes a bid suggestion for a callbreak hand.
    Port of the bid-related functions from bot_logic_prob.gd.
    """

    def __init__(self):
        self.total_bid_score = 0.0
        self.total_power_score = 0.0
        self.total_cut_score = 0.0
        self.spades_score_info = []
        self.confirm_score = 0.0
        self.projected_score = 0.0

        self._diamonds_cards = []
        self._clubs_cards = []
        self._hearts_cards = []
        self._spades_cards = []
        self._suit_cards = {
            Suit.DIAMONDS: self._diamonds_cards,
            Suit.CLUBS: self._clubs_cards,
            Suit.HEARTS: self._hearts_cards,
            Suit.SPADES: self._spades_cards,
        }

        self._total_from_spades = 0.0
        self._no_of_cards_evaluated_for_bid = 0.0
        self._no_of_cards_evaluated_from_spades = 0.0
        self._no_of_cards_evaluated_from_cut = 0.0
        self._no_of_cards_evaluated_from_power = 0.0
        self._spare_spades = 0.0

    def set_hand(self, spades, hearts, clubs, diamonds):
        """Set the hand by providing card ranks for each suit."""
        self._spades_cards = sorted(spades, reverse=True)
        self._hearts_cards = sorted(hearts, reverse=True)
        self._clubs_cards = sorted(clubs, reverse=True)
        self._diamonds_cards = sorted(diamonds, reverse=True)
        self._suit_cards = {
            Suit.DIAMONDS: self._diamonds_cards,
            Suit.CLUBS: self._clubs_cards,
            Suit.HEARTS: self._hearts_cards,
            Suit.SPADES: self._spades_cards,
        }

    def set_hand_from_cards(self, cards):
        """
        Set the hand from a list of card strings like ['14S', '13H', '12D', ...].
        """
        spades = []
        hearts = []
        clubs = []
        diamonds = []
        for card in cards:
            suit = SUIT_MAP[card[-1]]
            rank = int(card[:-1])
            if suit == Suit.SPADES:
                spades.append(rank)
            elif suit == Suit.HEARTS:
                hearts.append(rank)
            elif suit == Suit.CLUBS:
                clubs.append(rank)
            elif suit == Suit.DIAMONDS:
                diamonds.append(rank)
        self.set_hand(spades, hearts, clubs, diamonds)

    def clear_cards(self):
        """Clear all card data."""
        self._diamonds_cards.clear()
        self._clubs_cards.clear()
        self._hearts_cards.clear()
        self._spades_cards.clear()

    # -------------------------------------------------------------------------
    # Probability helpers
    # -------------------------------------------------------------------------
    def _prob_at_least(self, opp_at_least, my_total_cards):
        """Probability that all opponents have at least opp_at_least cards of this suit."""
        if opp_at_least == 0:
            return 1.0
        if my_total_cards in PROB_DIST:
            dist = PROB_DIST[my_total_cards]
            return float(dist.get(opp_at_least, 0.0))
        return 0.0

    # -------------------------------------------------------------------------
    # Spare spades evaluation
    # -------------------------------------------------------------------------
    def _evaluate_spare_spades(self):
        self._spare_spades = 1
        if 14 in self._spades_cards:
            self._spare_spades = 0.75
            if 13 in self._spades_cards:
                self._spare_spades = 0.5
                if 12 in self._spades_cards:
                    self._spare_spades = 0.25
                    if 11 in self._spades_cards:
                        self._spare_spades = 0
        else:
            # Without Ace, still give partial credit for other high spades.
            reduction = 0.0
            if 13 in self._spades_cards:
                reduction += 0.10
            if 12 in self._spades_cards:
                reduction += 0.08
            if 11 in self._spades_cards:
                reduction += 0.05
            self._spare_spades = max(0.55, self._spare_spades - reduction)

    # -------------------------------------------------------------------------
    # Power score (A/K/Q per suit)
    # -------------------------------------------------------------------------
    def _calculate_power_score(self, suit_cards):
        """
        Calculating sum of probabilities of winning with power cards.
        For a power card to win:
        Qn. What are the chances that all opponents have at least [x] cards?
        """
        m_prob_from_ace = 0
        m_prob_from_king = 0
        m_prob_from_queen = 0

        # If player has Ace
        if 14 in suit_cards:
            if len(suit_cards) <= 6:
                m_prob_from_ace = 1.0
                self._no_of_cards_evaluated_from_power += 1
            else:
                m_prob_from_ace = self._prob_at_least(1, len(suit_cards))
                self._no_of_cards_evaluated_from_power += 1

        # If player has King
        if 13 in suit_cards:
            # if i have less than 2 cards then return 0
            if len(suit_cards) >= 2:
                if len(suit_cards) <= 4:
                    m_prob_from_king = 1
                else:
                    m_prob_from_king = self._prob_at_least(2, len(suit_cards))
                self._no_of_cards_evaluated_from_power += 1
            else:
                m_prob_from_king = 0.0

        # If player has Queen
        if 12 in suit_cards:
            # if i have less than 3 cards then return 0
            if len(suit_cards) >= 3:
                m_prob_from_queen = self._prob_at_least(3, len(suit_cards))
            else:
                m_prob_from_queen = 0.0

        total_power = m_prob_from_ace + m_prob_from_king + m_prob_from_queen
        if total_power < 0.25:
            total_power = 0
        return total_power

    # -------------------------------------------------------------------------
    # Cut score
    # -------------------------------------------------------------------------
    def _calculate_cut_score(self, suit_cards):
        m_cut_prob = 0
        size = len(suit_cards)
        if size not in PROB_DIST:
            return 0
        m_dist = PROB_DIST[size]

        for m_atleast in m_dist:
            if m_atleast <= size:
                continue
            if m_dist[m_atleast] != 0:
                m_cut_prob += m_dist[m_atleast]
                if m_atleast != 4:
                    self._no_of_cards_evaluated_from_cut += 1
                if size == 2 and m_atleast == 3:
                    m_cut_prob += 0.2

        return min(2, m_cut_prob)

    # -------------------------------------------------------------------------
    # Spades scoring (confirmed + projected)
    # -------------------------------------------------------------------------
    def _get_opponents_total_higher_cards(self, rank, my_cards):
        count = 0
        for i in range(14, rank, -1):
            if i not in my_cards:
                count += 1
        return count

    def _get_opponents_total_higher_cards_from_remaining(self, card, my_spades, opp_remaining):
        count = 0
        for opp_card in opp_remaining:
            if opp_card > card:
                count += 1
        return count

    def _calculate_spades_score(self, my_spades_cards, total_cut_score):
        my_spades_cards = sorted(my_spades_cards, reverse=True)
        m_total_spades_score = 0.0
        is_confirmed = [False] * len(my_spades_cards)

        # First phase: cards with no higher opponents = sure win
        for i in range(len(my_spades_cards)):
            opp_higher = self._get_opponents_total_higher_cards(
                my_spades_cards[i], my_spades_cards
            )
            if opp_higher == 0:
                is_confirmed[i] = True
                m_total_spades_score += 1

        m_confirm_score = int(m_total_spades_score)

        # Second phase: pair remaining spades against opponent spades
        opp_remaining_spades = []
        for i in range(14, 1, -1):
            if i not in my_spades_cards:
                opp_remaining_spades.append(i)

        for i in range(len(my_spades_cards)):
            if is_confirmed[i]:
                continue
            if self._get_opponents_total_higher_cards_from_remaining(
                my_spades_cards[i], my_spades_cards, opp_remaining_spades
            ) >= 1:
                # Consumed - this spade absorbs an opponent's higher spade
                if len(opp_remaining_spades) > 0:
                    opp_remaining_spades.pop(0)
            else:
                # Survived - no higher opponents left, confirmed winner
                is_confirmed[i] = True
                m_confirm_score += 1

        self._no_of_cards_evaluated_from_spades += m_confirm_score
        m_total_spades_score = m_confirm_score

        # Projected scoring: add SPADE_PROBS for non-confirmed spades
        if total_cut_score + m_confirm_score < len(self._spades_cards) - self._spare_spades:
            cut_reserve = int(round(total_cut_score))
            consumed_indices = []
            for i in range(len(my_spades_cards)):
                if not is_confirmed[i]:
                    consumed_indices.append(i)
            for _k in range(min(cut_reserve, len(consumed_indices))):
                consumed_indices.pop()
            for idx in consumed_indices:
                m_total_spades_score += SPADE_PROBS.get(my_spades_cards[idx], 0)
                self._no_of_cards_evaluated_from_spades += 1

        if m_confirm_score == len(self._spades_cards):
            m_total_spades_score = m_confirm_score

        return [m_confirm_score, m_total_spades_score]

    # -------------------------------------------------------------------------
    # Spade dominance bonus
    # -------------------------------------------------------------------------
    def _calculate_spade_dominance_bonus(self, p_confirm_score, total_bid_score):
        total_spades = len(self._spades_cards)

        if total_spades < 4 or (total_spades == 4 and 14 not in self._spades_cards):
            return 0.0

        bonus = 0.0

        # Find spade rank quality
        spades_sorted = sorted(self._spades_cards, reverse=True)

        high_spade_count = 0
        rank_quality_score = 0.0
        for spade in spades_sorted:
            if spade >= 11:
                high_spade_count += 1
            rank_weight = max(0.0, min((spade - 2.0) / 12.0, 1.0))
            rank_quality_score += rank_weight

        avg_rank_quality = rank_quality_score / total_spades

        # Calculate spade volume bonus
        volume_bonus = 0.0
        if total_spades == 4:
            volume_bonus = 0.06
        elif total_spades == 5:
            volume_bonus = 0.15
        elif total_spades == 6:
            volume_bonus = 0.35
        else:
            volume_bonus = 0.60

        bonus += volume_bonus * (0.4 + 0.6 * avg_rank_quality)

        # confirmed high spade bonus
        for i in range(min(p_confirm_score, len(spades_sorted))):
            spade = spades_sorted[i]
            truly_top = True
            for r in range(14, spade, -1):
                if r not in self._spades_cards:
                    truly_top = False
                    break
            if not truly_top:
                continue

            # Scale bonus with spade count
            confirmed_mult = 0.35 if total_spades <= 5 else 0.75
            if spade >= 14:
                bonus += 0.15 * confirmed_mult
            elif spade >= 13:
                bonus += 0.15 * confirmed_mult
            elif spade >= 12:
                bonus += 0.12 * confirmed_mult
            elif spade >= 11:
                bonus += 0.10 * confirmed_mult

        # Each spade rank bonus for non-confirmed spades
        for i in range(p_confirm_score, len(spades_sorted)):
            spade = spades_sorted[i]
            if spade >= 12:
                bonus += 0.04
            elif spade >= 10:
                bonus += 0.06 * avg_rank_quality
            elif spade >= 7:
                bonus += 0.03 * avg_rank_quality
            else:
                # Low spades (2-6): valuable when higher cards available
                if total_spades >= 6:
                    bonus += 0.03 * (total_spades - 5) * 0.5

        # Non-spade suit volume
        total_non_spade_cards = 0
        for suit in [Suit.DIAMONDS, Suit.CLUBS, Suit.HEARTS]:
            total_non_spade_cards += len(self._suit_cards[suit])

        exhaustion_factor = 0.0
        if total_non_spade_cards >= 7:
            exhaustion_factor = 0.10
        elif total_non_spade_cards >= 5:
            exhaustion_factor = 0.06
        elif total_non_spade_cards >= 3:
            exhaustion_factor = 0.03

        for suit in [Suit.DIAMONDS, Suit.CLUBS, Suit.HEARTS]:
            suit_size = len(self._suit_cards[suit])
            if suit_size == 0:
                # Void suit: very strong cutting opportunity with many spades
                if total_spades >= 6:
                    bonus += 0.1 * avg_rank_quality
                elif total_spades >= 5:
                    bonus += 0.07 * avg_rank_quality
                continue
            suit_bonus = 0.0
            if suit_size >= 5:
                suit_bonus = 0.16
            elif suit_size >= 4:
                suit_bonus = 0.10
            elif suit_size >= 3:
                suit_bonus = 0.05
            elif suit_size >= 2:
                suit_bonus = 0.02
            bonus += suit_bonus * exhaustion_factor

        # Low bid adjustment
        expected_min_bid = total_spades - 2
        bid_gap = expected_min_bid - total_bid_score

        if bid_gap > 0:
            if avg_rank_quality >= 0.55:
                quality_multiplier = 0.12 + (avg_rank_quality - 0.55) * 0.25
                bonus += bid_gap * quality_multiplier
            elif avg_rank_quality >= 0.20:
                base_mult = 0.05
                if total_spades >= 5:
                    base_mult += (total_spades - 4) * 0.04
                bonus += bid_gap * base_mult

        # 6. Short suit cutting bonus for 4-5 spades
        if 4 <= total_spades <= 5:
            short_suit_count = 0
            for suit in [Suit.DIAMONDS, Suit.CLUBS, Suit.HEARTS]:
                if len(self._suit_cards[suit]) <= 2:
                    short_suit_count += 1
            if short_suit_count >= 1:
                bonus += short_suit_count * 0.08 * (0.5 + avg_rank_quality)

        # J bonus for multiple Js
        jack_count = 0
        for suit in [Suit.DIAMONDS, Suit.CLUBS, Suit.HEARTS, Suit.SPADES]:
            if 11 in self._suit_cards[suit]:
                jack_count += 1
        if jack_count >= 2:
            bonus += (jack_count - 1) * 0.08

        # Volume winners: with 6+ spades, opponents exhaust and remaining spades win
        if total_spades >= 6:
            opp_spades_total = 13 - total_spades
            opp_avg_per_player = opp_spades_total / 3.0
            contested_rounds = int(math.ceil(opp_avg_per_player))
            volume_winners = max(0, total_spades - contested_rounds - self._spare_spades)
            # projected_score already includes confirm_score, so only subtract projected
            uncounted_winners = min(1.0, max(0.0, volume_winners - self.projected_score))

            # Discount volume winners when highest spade is weak
            highest_spade = spades_sorted[0]
            quality_discount = 1.0
            if highest_spade < 11:
                quality_discount = 0.3  # Very weak: 10 or below
            elif highest_spade < 13:
                quality_discount = 0.6  # Moderate: J or Q only

            # cap bonus to prevent overbidding on 8+ spade hands
            winner_bonus = uncounted_winners * (0.45 + 0.4 * avg_rank_quality) * quality_discount
            bonus += min(0.8, winner_bonus)

        # Without Ace of spades, dominance is slightly limited
        if 14 not in self._spades_cards:
            bonus *= 0.5

        return bonus

    # -------------------------------------------------------------------------
    # Suit score aggregation
    # -------------------------------------------------------------------------
    def _calculate_suit_scores(self):
        """Calculate power and cut scores for all non-spade suits."""
        # Diamonds
        self.total_power_score += self._calculate_power_score(self._diamonds_cards)
        self.total_cut_score += self._calculate_cut_score(self._diamonds_cards)

        # Clubs
        self.total_power_score += self._calculate_power_score(self._clubs_cards)
        self.total_cut_score += self._calculate_cut_score(self._clubs_cards)

        # Hearts
        self.total_power_score += self._calculate_power_score(self._hearts_cards)
        self.total_cut_score += self._calculate_cut_score(self._hearts_cards)

    # -------------------------------------------------------------------------
    # Low spade adjustments
    # -------------------------------------------------------------------------
    def _apply_low_spade_adjustments(self):
        """Apply adjustments for hands with few spades."""
        if len(self._spades_cards) < 4:
            if self.total_cut_score < 0.7:
                self.total_cut_score = 0
            elif self.total_cut_score >= 1.0:
                # Only count cuts for spades that can reliably win a cut
                reliable_cut_spades = 0
                for spade in self._spades_cards:
                    if spade >= 10:
                        reliable_cut_spades += 1
                max_reliable_cuts = max(0.5, reliable_cut_spades * 0.7)
                self.total_cut_score = min(self.total_cut_score, max_reliable_cuts)

    # -------------------------------------------------------------------------
    # Filter weak projections
    # -------------------------------------------------------------------------
    def _filter_weak_projections(self):
        """Filter out unreliable projected scores from weak spade hands."""
        if len(self._spades_cards) < 3 and not (
            14 in self._spades_cards or 13 in self._spades_cards
        ):
            self.projected_score = 0

        # Very low spades (all below 10)
        if len(self._spades_cards) <= 3 and not (
            14 in self._spades_cards or 13 in self._spades_cards
        ):
            highest_spade = max(self._spades_cards) if len(self._spades_cards) > 0 else 0
            if highest_spade < 10:
                self.projected_score = 0

        if self._no_of_cards_evaluated_from_spades < 4 and self.projected_score < 0.36:
            self.projected_score = 0

    # -------------------------------------------------------------------------
    # Cards evaluated count
    # -------------------------------------------------------------------------
    def _calculate_cards_evaluated_count(self):
        """Calculate total number of cards evaluated for bid."""
        if (
            self._no_of_cards_evaluated_from_cut + self._no_of_cards_evaluated_from_spades
            > len(self._spades_cards)
        ):
            if self._no_of_cards_evaluated_from_spades == len(self._spades_cards):
                self._no_of_cards_evaluated_for_bid = (
                    len(self._spades_cards) + self._no_of_cards_evaluated_from_power
                )
            else:
                self._no_of_cards_evaluated_for_bid = math.ceil(
                    len(self._spades_cards)
                    - self._spare_spades
                    + self._no_of_cards_evaluated_from_power
                )
        else:
            self._no_of_cards_evaluated_for_bid = (
                self._no_of_cards_evaluated_from_power
                + self._no_of_cards_evaluated_from_cut
                + self._no_of_cards_evaluated_from_spades
            )

    # -------------------------------------------------------------------------
    # Final cut adjustments
    # -------------------------------------------------------------------------
    def _apply_final_cut_adjustments(self):
        """Apply final adjustments to cut score."""
        # Cap cuts by remaining spades
        if self.total_cut_score > len(self._spades_cards) - self.projected_score - self._spare_spades:
            self.total_cut_score = len(self._spades_cards) - self.projected_score - self._spare_spades
            if self.total_cut_score < 0:
                self.total_cut_score = 0

        # Zero cuts if no spades available
        cut_available_spades_count = len(self._spades_cards) - round(self.projected_score)
        if (
            cut_available_spades_count - round(self.total_cut_score) < 1
            and len(self._spades_cards) < 4
        ):
            self.total_cut_score = 0

        # Discount by spade quality
        self._apply_spade_quality_discount()

        # Handle cut/spade overlap
        self._handle_cut_spade_overlap()

    def _apply_spade_quality_discount(self):
        if self.total_cut_score > 0 and len(self._spades_cards) >= 4:
            spades_sorted = sorted(self._spades_cards, reverse=True)

            projected_spades_used = int(round(self.projected_score))
            cutting_spade_start = min(projected_spades_used, len(spades_sorted))

            if cutting_spade_start < len(spades_sorted):
                # Calculate volume factor
                volume_factor = 1.0
                if len(self._spades_cards) >= 7:
                    volume_factor = 3.0
                elif len(self._spades_cards) >= 6:
                    volume_factor = 2.0
                elif len(self._spades_cards) >= 5:
                    volume_factor = 1.4

                # Calculate cut qualities
                cut_qualities = []
                for i in range(cutting_spade_start, len(spades_sorted)):
                    spade_rank = spades_sorted[i]
                    if spade_rank >= 12:
                        cut_success = 0.80 + (spade_rank - 12) * 0.08
                    elif spade_rank >= 8:
                        cut_success = 0.40 + (spade_rank - 8) * 0.10
                    else:
                        cut_success = max(0.10, (spade_rank - 2) * 0.06 + 0.10)
                    cut_success = min(0.95, cut_success * volume_factor)
                    cut_qualities.append(cut_success)

                # Apply discount strategy
                if len(cut_qualities) > 0:
                    if len(self._spades_cards) <= 6:
                        # Best-first strategy for 4-6 spades
                        remaining_cut = self.total_cut_score
                        discounted_cut = 0.0
                        for q in cut_qualities:
                            if remaining_cut <= 0:
                                break
                            this_cut = min(remaining_cut, 1.0)
                            discounted_cut += this_cut * q
                            remaining_cut -= this_cut
                        self.total_cut_score = discounted_cut
                    else:
                        # Average quality for 7+ spades
                        quality_sum = sum(cut_qualities)
                        self.total_cut_score *= quality_sum / len(cut_qualities)

    def _handle_cut_spade_overlap(self):
        if (
            len(self._spades_cards) <= 3
            and 14 not in self._spades_cards
            and 13 not in self._spades_cards
        ):
            spade_capacity = float(len(self._spades_cards)) - self._spare_spades
            if spade_capacity > 0:
                spade_usage = self.total_cut_score + self.projected_score
                if spade_usage > spade_capacity * 0.6:
                    excess = spade_usage - spade_capacity * 0.6
                    self.total_cut_score = max(0, self.total_cut_score - excess)

    # -------------------------------------------------------------------------
    # Vulnerable kings penalty
    # -------------------------------------------------------------------------
    def _apply_vulnerable_kings_penalty(self):
        if len(self._spades_cards) <= 3:
            vulnerable_king_count = 0
            has_any_ace = False
            for suit in [Suit.DIAMONDS, Suit.CLUBS, Suit.HEARTS]:
                if 14 in self._suit_cards[suit]:
                    has_any_ace = True
                if (
                    13 in self._suit_cards[suit]
                    and 14 not in self._suit_cards[suit]
                    and len(self._suit_cards[suit]) >= 3
                ):
                    vulnerable_king_count += 1
            if not has_any_ace and vulnerable_king_count >= 2:
                self.total_bid_score -= vulnerable_king_count * 0.3

    # -------------------------------------------------------------------------
    # Main bid score computation
    # -------------------------------------------------------------------------
    def get_total_cards_win_probabilities(self):
        self.total_bid_score = 0
        self.total_power_score = 0
        self.total_cut_score = 0

        self._init_cards_evaluated()
        self._evaluate_spare_spades()

        self._calculate_suit_scores()

        # Limit cuts to available spades
        self.total_cut_score = min(
            self.total_cut_score, len(self._spades_cards) - self._spare_spades
        )

        self._apply_low_spade_adjustments()

        self.spades_score_info = self._calculate_spades_score(
            self._spades_cards, self.total_cut_score
        )
        self.confirm_score = self.spades_score_info[0]
        self.projected_score = self.spades_score_info[1]

        self._filter_weak_projections()
        self._total_from_spades = int(round(self.projected_score))

        self._calculate_cards_evaluated_count()

        self._apply_final_cut_adjustments()

        if self.total_power_score - math.floor(self.total_power_score) < 0.3:
            self.total_power_score = math.floor(self.total_power_score)

        # Calculate total bid score
        self.total_bid_score = (
            self.total_power_score + self.total_cut_score + self.projected_score
        )

        # Add spade dominance bonus
        spade_dominance_score = self._calculate_spade_dominance_bonus(
            self.confirm_score, self.total_bid_score
        )
        self.total_bid_score += spade_dominance_score

        # Apply vulnerable kings penalty
        self._apply_vulnerable_kings_penalty()

        return self.total_bid_score

    def _init_cards_evaluated(self):
        self._no_of_cards_evaluated_for_bid = 0
        self._no_of_cards_evaluated_from_spades = 0
        self._no_of_cards_evaluated_from_cut = 0
        self._no_of_cards_evaluated_from_power = 0

    # -------------------------------------------------------------------------
    # Queen probability removal for high bids
    # -------------------------------------------------------------------------
    def _remove_queen_probs(self, total_bid_score):
        for suit in [Suit.DIAMONDS, Suit.CLUBS, Suit.HEARTS]:
            if 12 in self._suit_cards[suit]:
                if len(self._suit_cards[suit]) >= 3:
                    total_bid_score -= self._prob_at_least(3, len(self._suit_cards[suit]))
        return total_bid_score

    # -------------------------------------------------------------------------
    # Context adjustments for projected score
    # -------------------------------------------------------------------------
    def projected_score_context_adjustments(self, my_total_bid_score, total_bid_this_round):
        if my_total_bid_score >= 4:
            if round(total_bid_this_round + my_total_bid_score) == 12:
                my_total_bid_score -= 0.15
            if math.floor(total_bid_this_round + my_total_bid_score) == 10:
                my_total_bid_score += 0.15
        return my_total_bid_score

    # -------------------------------------------------------------------------
    # Evaluate game mode last round
    # -------------------------------------------------------------------------
    def _evaluate_last_game_round(self, match_game_mode):
        if match_game_mode == GameMode.STANDARD:
            return 4
        else:
            return 2

    # -------------------------------------------------------------------------
    # Final bid decision with thresholds
    # -------------------------------------------------------------------------
    def _decide_final_bid(
        self,
        p_total_bid_score,
        p_last_game_round,
        p_total_bid_this_round,
        p_total_cut_score,
        p_match_current_game_round,
        p_match_current_bid_turn,
        p_match_dealer,
        p_g_total_scores,
        logic_holder=None,
        match_players=None,
        game_helper=None,
    ):
        m_bid_final = 0
        total_num_of_spades = len(self._spades_cards)

        diff = self._no_of_cards_evaluated_for_bid - p_total_bid_score
        threshold = 0.0

        if diff <= 0.15:
            threshold = 0.5
            if p_total_bid_score >= 8:
                # Very high bids: be extremely conservative
                threshold = 0.90

        # No of spades low cause lower spades mean low chance to overcut and low bid score
        elif diff >= 1.0 and total_num_of_spades < 4 and p_total_bid_score < 5:
            if diff > 3:
                threshold = 0.90
            else:
                # When spades have no A or K, projected spade score is unreliable
                if 14 not in self._spades_cards and 13 not in self._spades_cards:
                    threshold = 0.85
                elif (p_total_bid_score - self.projected_score) < 0.5:
                    # With few spades, projected-only bids are less reliable
                    if total_num_of_spades <= 3:
                        threshold = 0.55
                    else:
                        threshold = 0.5
                else:
                    threshold = 0.72
        else:
            if p_total_bid_score >= 8:
                threshold = 0.90
            elif p_total_bid_score >= 7:
                threshold = 0.86
            elif p_total_bid_score >= 4 and total_num_of_spades > 3:
                # Hands with many spades but missing both A and K can't guarantee win
                if (
                    14 not in self._spades_cards
                    and 13 not in self._spades_cards
                    and total_num_of_spades >= 4
                ):
                    if self.confirm_score == 0:
                        threshold = 0.85
                    else:
                        threshold = 0.7
                elif (
                    14 not in self._spades_cards
                    and 13 in self._spades_cards
                    and total_num_of_spades >= 5
                ):
                    # Has K but not A, K can be beaten, slightly cautious
                    threshold = 0.65
                elif total_num_of_spades >= 5:
                    # A+K confirmed but remaining spades are weak
                    if (
                        14 in self._spades_cards
                        and 13 in self._spades_cards
                        and p_total_bid_score >= 5.5
                    ):
                        threshold = 0.95
                    else:
                        threshold = 0.5
                else:
                    threshold = 0.6
            elif total_num_of_spades >= 5:
                # 5 spades without A/K - be more conservative
                if 14 not in self._spades_cards and 13 not in self._spades_cards:
                    threshold = 0.85
                else:
                    threshold = 0.5
            elif total_num_of_spades == 4:
                # Without confirmed spade winners or A/K
                if (
                    self.confirm_score == 0
                    and 14 not in self._spades_cards
                    and 13 not in self._spades_cards
                ):
                    threshold = 0.85
                else:
                    threshold = 0.55
            else:
                threshold = 0.7

        fractional = p_total_bid_score - math.floor(p_total_bid_score)

        if fractional >= threshold:
            m_bid_final = int(max(1, math.ceil(p_total_bid_score)))
        else:
            m_bid_final = int(max(1, math.floor(p_total_bid_score)))

        # Last round re-evaluation (simplified - needs game context)
        # In standalone mode, we skip the last-round and last-turn re-evaluations
        # since they require opponent data from the game state.

        return int(max(1, m_bid_final))

    # -------------------------------------------------------------------------
    # Last round re-evaluation
    # -------------------------------------------------------------------------
    def last_round_re_evaluate(
        self, my_bid, current_bid_turn, my_in_game_id, g_total_scores, opponents_info
    ):
        """
        Re-evaluate bid on the last game round using opponent projected scores.
        opponents_info: list of dicts with 'in_game_id' and 'bid' keys.
        """
        my_projected_score = g_total_scores.get(my_in_game_id, 0) + my_bid
        opponents_projected = []
        for opp in opponents_info:
            opponents_projected.append(
                g_total_scores.get(opp["in_game_id"], 0) + opp["bid"]
            )

        if current_bid_turn == 1:
            if (
                opponents_projected[0] > my_projected_score
                and opponents_projected[0] - my_projected_score < 1
            ):
                my_bid += 1

        if current_bid_turn == 2:
            if (
                (
                    opponents_projected[0] > my_projected_score
                    and opponents_projected[0] - my_projected_score < 1
                )
                or (
                    opponents_projected[1] > my_projected_score
                    and opponents_projected[1] - my_projected_score < 1
                )
            ):
                my_bid += 1
            elif (
                my_projected_score > opponents_projected[0] + 2
                and my_projected_score > opponents_projected[1] + 2
            ):
                my_bid -= 1

        if current_bid_turn == 3:
            if (
                (
                    opponents_projected[0] > my_projected_score
                    and opponents_projected[0] - my_projected_score < 1
                )
                or (
                    opponents_projected[1] > my_projected_score
                    and opponents_projected[1] - my_projected_score < 1
                )
                or (
                    opponents_projected[2] > my_projected_score
                    and opponents_projected[2] - my_projected_score < 1
                )
            ):
                my_bid += 1
            elif (
                my_projected_score > opponents_projected[0] + 2
                and my_projected_score > opponents_projected[1] + 2
                and my_projected_score > opponents_projected[2] + 2
            ):
                my_bid -= 1

        my_projected_score = g_total_scores.get(my_in_game_id, 0) + my_bid
        if my_projected_score < 0 and 0 - my_projected_score < 3:
            my_bid += 0 - my_projected_score

        return int(round(my_bid))

    # -------------------------------------------------------------------------
    # Last turn re-evaluation
    # -------------------------------------------------------------------------
    def last_turn_re_evaluate(
        self, projected_total, my_total, total_cut_score, bid_final
    ):
        suit_cards = {
            Suit.DIAMONDS: self._diamonds_cards,
            Suit.CLUBS: self._clubs_cards,
            Suit.HEARTS: self._hearts_cards,
            Suit.SPADES: self._spades_cards,
        }

        if projected_total <= 7:
            for suit in [Suit.DIAMONDS, Suit.CLUBS, Suit.HEARTS]:
                if 13 in suit_cards[suit]:
                    if 12 in suit_cards[suit] or 14 in suit_cards[suit]:
                        my_total += 1 - self._prob_at_least(2, len(suit_cards[suit]))
                    elif len(suit_cards[suit]) >= 2:
                        my_total += 0.862628 - self._prob_at_least(
                            2, len(suit_cards[suit])
                        )
                if 12 in suit_cards[suit]:
                    if len(suit_cards[suit]) in [3, 4]:
                        my_total += 0.27687 - self._prob_at_least(
                            3, len(suit_cards[suit])
                        )
                if len(suit_cards[suit]) <= 2:
                    if (
                        len(suit_cards[Suit.SPADES])
                        - (self._total_from_spades + total_cut_score)
                        > 1
                    ):
                        my_total += 1 - 0.650359

        if projected_total >= 13:
            for suit in [Suit.DIAMONDS, Suit.CLUBS, Suit.HEARTS]:
                if 14 in suit_cards[suit]:
                    my_total -= 1 - self._prob_at_least(1, len(suit_cards[suit]))
                if 13 in suit_cards[suit]:
                    if len(suit_cards[suit]) == 2:
                        my_total -= 0.4 * self._prob_at_least(
                            2, len(suit_cards[suit])
                        )
                    if len(suit_cards[suit]) == 3:
                        my_total -= 0.6 * self._prob_at_least(
                            2, len(suit_cards[suit])
                        )
                    if len(suit_cards[suit]) == 4:
                        my_total -= 0.8 * self._prob_at_least(
                            2, len(suit_cards[suit])
                        )
                    if len(suit_cards[suit]) > 4:
                        my_total -= self._prob_at_least(2, len(suit_cards[suit]))
                if 12 in suit_cards[suit]:
                    my_total -= self._prob_at_least(3, len(suit_cards[suit]))

        return int(round(my_total))

    # -------------------------------------------------------------------------
    # Main entry point: select_bid_amount
    # -------------------------------------------------------------------------
    def select_bid_amount(
        self,
        game_mode=GameMode.STANDARD,
        current_game_round=1,
        current_bid_turn=0,
        dealer=None,
        g_total_scores=None,
        total_bid_this_round=0,
    ):
        """
        Main entry point to compute bid amount.
        Returns an integer bid value.
        """
        if g_total_scores is None:
            g_total_scores = {}

        total_bid_score = self.get_total_cards_win_probabilities()

        self._total_from_spades = self.total_cut_score + self.projected_score

        # removing queen probs in high bid case
        if total_bid_score >= 5:
            total_bid_score = self._remove_queen_probs(total_bid_score)

        last_game_round = self._evaluate_last_game_round(game_mode)

        if current_bid_turn == 3:
            total_bid_score = self.projected_score_context_adjustments(
                total_bid_score, total_bid_this_round
            )

        return self._decide_final_bid(
            total_bid_score,
            last_game_round,
            total_bid_this_round,
            self.total_cut_score,
            current_game_round,
            current_bid_turn,
            dealer,
            g_total_scores,
        )

    # -------------------------------------------------------------------------
    # Compute bid from card strings
    # -------------------------------------------------------------------------
    def compute_bid(self, cards, game_mode=GameMode.STANDARD):
        """
        Convenience method: set hand from card strings and compute bid.
        cards: list of card strings like ['14S', '13H', '12D', ...]
        """
        self.set_hand_from_cards(cards)
        return self.select_bid_amount(game_mode=game_mode)
