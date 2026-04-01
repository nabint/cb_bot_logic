# -----------
# Bot play/throw logic ported from bot_logic_prob.gd
# Handles card throwing strategies, play strategies, game event handling.
# -----------

import random
import math
from bid_logic import Suit, Strategy, SUIT_MAP, SUIT_LETTER, SPADE_PROBS, PROB_DIST, BidLogic


class GameHelper:
    """
    Minimal game helper that provides utility methods needed by the bot logic.
    In the original GDScript this was a separate class injected into the bot.
    """

    @staticmethod
    def get_winning_card(played_cards):
        """
        Determine the winning card from a list of card strings.
        The first card determines the led suit.
        Spades (trump) beat any non-trump.
        Highest card of the led suit wins unless trumped.
        """
        if not played_cards:
            return None

        led_suit = SUIT_MAP[played_cards[0][-1]]
        best_card = played_cards[0]
        best_rank = int(played_cards[0][:-1])
        best_suit = led_suit

        for card in played_cards[1:]:
            card_suit = SUIT_MAP[card[-1]]
            card_rank = int(card[:-1])

            if card_suit == Suit.SPADES and best_suit != Suit.SPADES:
                # Trump beats non-trump
                best_card = card
                best_rank = card_rank
                best_suit = card_suit
            elif card_suit == best_suit:
                if card_rank > best_rank:
                    best_card = card
                    best_rank = card_rank
            # If card is off-suit and not trump, it can't win

        return best_card

    @staticmethod
    def get_right_side_player(player, match_players):
        """Get the player to the right (next player)."""
        idx = match_players.index(player)
        return match_players[(idx + 1) % len(match_players)]

    @staticmethod
    def get_bid_player(dealer, match_players, bid_turn):
        """Get the player at the given bid turn position relative to dealer."""
        dealer_idx = match_players.index(dealer)
        return match_players[(dealer_idx + 1 + bid_turn) % len(match_players)]


class Player:
    """Represents a player in the callbreak game."""

    def __init__(self, in_game_id, username="Bot"):
        self.in_game_id = in_game_id
        self.username = username
        self.bid = 0
        self.hands = 0  # tricks won
        self.cards = []
        self.legal_cards = []
        self.score = 0.0

    def is_me(self):
        """In standalone mode, all players are bots."""
        return False


class BotLogic:
    """
    Bot logic for card play/throw decisions.
    Port of the play-related functions from bot_logic_prob.gd.
    """

    def __init__(self, logic_holder, game_helper=None, match_players=None, skip_bid_logic=False):
        self._logic_holder = logic_holder
        self._game_helper = game_helper or GameHelper()
        self._match_players = match_players or []

        self._initial_cards_set = []
        self._unrevealed_cards = {}

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

        self._play_strategies = []
        self._game_starter = None
        self._total_from_spades = 0.0
        self._spare_spades = 0.0

        self._cut_trick_played = {
            Suit.DIAMONDS: 0,
            Suit.CLUBS: 0,
            Suit.HEARTS: 0,
        }
        self._suit_play_turns = {
            Suit.DIAMONDS: 0,
            Suit.CLUBS: 0,
            Suit.HEARTS: 0,
            Suit.SPADES: 0,
        }

        # Bid logic instance for computing bids (skip during rollouts for performance)
        self._bid_logic = None if skip_bid_logic else BidLogic()

    # -------------------------------------------------------------------------
    # Card setup and management
    # -------------------------------------------------------------------------
    def on_deal_completed(self):
        """Called when cards have been dealt."""
        self._initial_cards_set = self._logic_holder.cards[:]
        self._init_unrevealed_cards()
        self._separate_cards_according_to_suits()

    def _init_unrevealed_cards(self):
        self._unrevealed_cards = {
            Suit.DIAMONDS: list(range(14, 1, -1)),
            Suit.CLUBS: list(range(14, 1, -1)),
            Suit.HEARTS: list(range(14, 1, -1)),
            Suit.SPADES: list(range(14, 1, -1)),
        }

    def _separate_cards_according_to_suits(self):
        self._diamonds_cards.clear()
        self._clubs_cards.clear()
        self._hearts_cards.clear()
        self._spades_cards.clear()

        for card in self._logic_holder.cards:
            suit = SUIT_MAP[card[-1]]
            rank = int(card[:-1])
            if suit == Suit.SPADES:
                self._spades_cards.append(rank)
            elif suit == Suit.DIAMONDS:
                self._diamonds_cards.append(rank)
            elif suit == Suit.CLUBS:
                self._clubs_cards.append(rank)
            elif suit == Suit.HEARTS:
                self._hearts_cards.append(rank)

        self._spades_cards.sort(reverse=True)
        self._diamonds_cards.sort(reverse=True)
        self._clubs_cards.sort(reverse=True)
        self._hearts_cards.sort(reverse=True)

    def _clear_bot_logic_data(self):
        self._play_strategies.clear()
        self._diamonds_cards.clear()
        self._hearts_cards.clear()
        self._clubs_cards.clear()
        self._spades_cards.clear()

        for suit in self._suit_play_turns:
            self._suit_play_turns[suit] = 0
        for suit in self._cut_trick_played:
            self._cut_trick_played[suit] = 0

    # -------------------------------------------------------------------------
    # Bid computation (delegates to BidLogic)
    # -------------------------------------------------------------------------
    def compute_bid(self):
        """Compute bid for the current hand."""
        self._bid_logic.set_hand(
            self._spades_cards[:],
            self._hearts_cards[:],
            self._clubs_cards[:],
            self._diamonds_cards[:],
        )
        return self._bid_logic.select_bid_amount()

    # -------------------------------------------------------------------------
    # Utility methods
    # -------------------------------------------------------------------------
    def _get_suit_letter(self, suit_id):
        return SUIT_LETTER.get(suit_id, "")

    def _max_arr(self, arr):
        assert len(arr) > 0
        return max(arr)

    def _min_arr(self, arr):
        assert len(arr) > 0
        return min(arr)

    def _get_senior_card(self, suit_id):
        """Get the highest unrevealed card in a suit."""
        if len(self._unrevealed_cards.get(suit_id, [])) > 0:
            return self._unrevealed_cards[suit_id][0]
        return 0

    def _get_largest_of_mycards_from_this_suit(self, suit_id):
        assert len(self._logic_holder.legal_cards) > 0
        card_ranks = []
        for card in self._logic_holder.cards:
            if SUIT_MAP[card[-1]] != suit_id:
                continue
            card_ranks.append(int(card[:-1]))
        assert len(card_ranks) > 0
        return max(card_ranks)

    def _get_2nd_largest(self, arr):
        if len(arr) < 2:
            return arr[0]
        sorted_arr = sorted(arr)
        return sorted_arr[-2]

    def _is_suit_has_king(self, suit_cards):
        return 13 in suit_cards

    def _is_suit_has_ace(self, suit_cards):
        return 14 in suit_cards

    def _is_suit_has_queen(self, suit_cards):
        return 12 in suit_cards

    def _is_all_spades_in_legal_cards(self):
        for card in self._logic_holder.legal_cards:
            if SUIT_MAP[card[-1]] != Suit.SPADES:
                return False
        return True

    def _get_smallest_from_legal_cards(self, match_played_cards):
        legal_cards_ranks = []
        if self._is_all_spades_in_legal_cards():
            for card in self._logic_holder.legal_cards:
                legal_cards_ranks.append(int(card[:-1]))
        else:
            for card in self._logic_holder.legal_cards:
                if SUIT_MAP[card[-1]] != Suit.SPADES:
                    legal_cards_ranks.append(int(card[:-1]))
        return str(min(legal_cards_ranks))

    def _get_random_card(self, cards):
        card = cards[random.randint(0, len(cards) - 1)]
        suit_id = SUIT_MAP[card[-1]]
        rand_gen_count = 0

        if len(self._unrevealed_cards[suit_id]) == 1:
            return card

        while (
            len(self._unrevealed_cards[suit_id]) > 1
            and self._unrevealed_cards[suit_id][1] == int(card[:-1])
        ):
            if len(cards) == 1:
                break
            if rand_gen_count >= 20:
                break
            card = cards[random.randint(0, len(cards) - 1)]
            suit_id = SUIT_MAP[card[-1]]
            rand_gen_count += 1
            if len(self._unrevealed_cards[suit_id]) == 1:
                return card

        return card

    def _is_there_a_cut_trick(self, match_played_cards):
        card1 = match_played_cards[1]
        card2 = match_played_cards[2]
        return (
            SUIT_MAP[card1[-1]] == Suit.SPADES
            or SUIT_MAP[card2[-1]] == Suit.SPADES
        )

    # -------------------------------------------------------------------------
    # Confuser card
    # -------------------------------------------------------------------------
    def get_confuser_card(self, smallest_rank, suit):
        confuser_card = smallest_rank
        suit_cards = self._suit_cards[suit]
        if len(suit_cards) == 0:
            return str(confuser_card) + self._get_suit_letter(suit)
        for rank in range(smallest_rank + 1, suit_cards[0] + 1):
            if rank in self._unrevealed_cards[suit]:
                if rank in suit_cards:
                    confuser_card = rank
                else:
                    return str(confuser_card) + self._get_suit_letter(suit)
        return str(confuser_card) + self._get_suit_letter(suit)

    # -------------------------------------------------------------------------
    # On cut chance but no spades
    # -------------------------------------------------------------------------
    def _on_cut_chance_but_have_no_spades(self, first_throw_card_suit):
        suit_sizes = {
            Suit.DIAMONDS: 0,
            Suit.CLUBS: 0,
            Suit.HEARTS: 0,
            Suit.SPADES: 0,
        }
        for suit in [Suit.DIAMONDS, Suit.CLUBS, Suit.HEARTS]:
            suit_sizes[suit] = len(self._suit_cards[suit])

        max_suit_size = max(suit_sizes[s] for s in [Suit.DIAMONDS, Suit.CLUBS, Suit.HEARTS])
        max_size_suit_ids = []
        for suit in [Suit.DIAMONDS, Suit.CLUBS, Suit.HEARTS]:
            if suit_sizes[suit] == max_suit_size:
                max_size_suit_ids.append(suit)

        if len(max_size_suit_ids) == 1:
            return (
                str(self._min_arr(self._suit_cards[max_size_suit_ids[0]]))
                + self._get_suit_letter(max_size_suit_ids[0])
            )

        if len(max_size_suit_ids) == 2:
            rank = min(
                self._min_arr(self._suit_cards[max_size_suit_ids[0]]),
                self._min_arr(self._suit_cards[max_size_suit_ids[1]]),
            )
            if rank == self._min_arr(self._suit_cards[max_size_suit_ids[0]]):
                return str(rank) + self._get_suit_letter(max_size_suit_ids[0])
            else:
                return str(rank) + self._get_suit_letter(max_size_suit_ids[1])

        if len(max_size_suit_ids) == 3:
            mins = [
                self._min_arr(self._suit_cards[max_size_suit_ids[0]]),
                self._min_arr(self._suit_cards[max_size_suit_ids[1]]),
                self._min_arr(self._suit_cards[max_size_suit_ids[2]]),
            ]
            rank = min(mins)
            if rank == mins[0]:
                return str(rank) + self._get_suit_letter(max_size_suit_ids[0])
            elif rank == mins[1]:
                return str(rank) + self._get_suit_letter(max_size_suit_ids[1])
            else:
                return str(rank) + self._get_suit_letter(max_size_suit_ids[2])

        return ""

    # -------------------------------------------------------------------------
    # Play strategies
    # -------------------------------------------------------------------------
    def _create_play_strategies(self):
        for suit in [Suit.DIAMONDS, Suit.CLUBS, Suit.HEARTS]:
            if len(self._suit_cards[suit]) != 0 and self._suit_play_turns[suit] == 0:
                if not self._is_suit_has_ace(self._suit_cards[suit]):
                    if self._is_suit_has_king(self._suit_cards[suit]):
                        if not self._is_suit_has_queen(self._suit_cards[suit]):
                            if len(self._suit_cards[suit]) > 2:
                                self._play_strategies.append(
                                    {Strategy.BRING_DOWN_ACE: suit}
                                )
                            if len(self._suit_cards[suit]) == 2:
                                if self._get_2nd_largest(self._suit_cards[suit]) >= 7:
                                    self._play_strategies.append(
                                        {Strategy.BRING_DOWN_ACE: suit}
                                    )
                                else:
                                    self._play_strategies.append(
                                        {Strategy.PREPARE_FOR_CUT: suit}
                                    )
                        elif self._is_suit_has_queen(self._suit_cards[suit]):
                            if len(self._suit_cards[suit]) >= 2:
                                self._play_strategies.append(
                                    {Strategy.BRING_DOWN_KING: suit}
                                )
                            else:
                                self._play_strategies.append(
                                    {Strategy.PREPARE_FOR_CUT: suit}
                                )
                    elif len(self._suit_cards[suit]) <= 2:
                        self._play_strategies.append(
                            {Strategy.PREPARE_FOR_CUT: suit}
                        )

        if (
            self._suit_play_turns[Suit.SPADES] == 0
            and len(self._unrevealed_cards[Suit.SPADES]) == 13
        ):
            if 14 in self._spades_cards and 13 in self._spades_cards:
                self._play_strategies.append({Strategy.COMPETE_SPADES: Suit.SPADES})
                if 12 in self._spades_cards:
                    self._play_strategies.append(
                        {Strategy.COMPETE_SPADES: Suit.SPADES}
                    )
                    if 11 in self._spades_cards:
                        self._play_strategies.append(
                            {Strategy.COMPETE_SPADES: Suit.SPADES}
                        )
                return

            if (
                len(self._spades_cards) - self._total_from_spades
                >= self._spare_spades
            ):
                if self._logic_holder.bid < 6:
                    for _ in range(
                        int(
                            len(self._spades_cards)
                            - self._total_from_spades
                            - int(round(self._spare_spades))
                        )
                    ):
                        self._play_strategies.append(
                            {Strategy.COMPETE_SPADES: Suit.SPADES}
                        )
                elif (
                    len(self._spades_cards) - self._total_from_spades
                    >= self._spare_spades
                ):
                    for _ in range(
                        int(
                            len(self._spades_cards)
                            - self._total_from_spades
                            - int(round(self._spare_spades))
                        )
                    ):
                        self._play_strategies.append(
                            {Strategy.COMPETE_SPADES: Suit.SPADES}
                        )

    def _get_play_strategy_card(self, play_strategy):
        for key in play_strategy:
            suit_val = play_strategy[key]
            if suit_val == Suit.DIAMONDS:
                suit_cards = self._diamonds_cards
            elif suit_val == Suit.CLUBS:
                suit_cards = self._clubs_cards
            elif suit_val == Suit.HEARTS:
                suit_cards = self._hearts_cards
            else:
                suit_cards = self._spades_cards

            if key == Strategy.BRING_DOWN_ACE:
                return (
                    str(self._bring_down_ace(suit_cards, suit_val))
                    + self._get_suit_letter(suit_val)
                )
            if key == Strategy.BRING_DOWN_KING:
                return (
                    str(self._bring_down_king(suit_cards, suit_val))
                    + self._get_suit_letter(suit_val)
                )
            if key == Strategy.PREPARE_FOR_CUT:
                return (
                    str(self._prepare_for_cut(suit_cards, suit_val))
                    + self._get_suit_letter(suit_val)
                )
            if key == Strategy.COMPETE_SPADES:
                return str(self._compete_spades()) + self._get_suit_letter(suit_val)

        return ""

    def _bring_down_ace(self, suit_cards, suit):
        if (
            self._get_senior_card(suit) in suit_cards
            and self._cut_trick_played[suit] == 0
        ):
            return self._get_senior_card(suit)
        else:
            return self._get_2nd_largest(suit_cards)

    def _bring_down_king(self, suit_cards, suit):
        if (
            self._get_senior_card(suit) in suit_cards
            and self._cut_trick_played[suit] == 0
        ):
            return self._get_senior_card(suit)
        else:
            return self._get_2nd_largest(suit_cards)

    def _prepare_for_cut(self, suit_cards, suit):
        if self._get_senior_card(suit) in suit_cards:
            return self._get_senior_card(suit)
        else:
            return self._get_2nd_largest(suit_cards)

    def _compete_spades(self):
        if (
            len(self._spades_cards) > 1
            and self._get_senior_card(Suit.SPADES) in self._spades_cards
            and len(self._unrevealed_cards[Suit.SPADES]) > 1
            and self._unrevealed_cards[Suit.SPADES][1] in self._spades_cards
        ):
            return self._get_senior_card(Suit.SPADES)
        if (
            len(self._spades_cards) > 1
            and self._get_senior_card(Suit.SPADES) in self._spades_cards
            and (
                len(self._unrevealed_cards[Suit.SPADES]) - len(self._spades_cards)
                <= len(self._spades_cards)
            )
        ):
            return self._get_senior_card(Suit.SPADES)
        elif 10 in self._spades_cards and self._get_largest_of_mycards_from_this_suit(Suit.SPADES) != 10:
            return 10
        elif 9 in self._spades_cards and self._get_largest_of_mycards_from_this_suit(Suit.SPADES) != 9:
            return 9
        else:
            return self._min_arr(self._spades_cards)

    # -------------------------------------------------------------------------
    # Card removal / undo
    # -------------------------------------------------------------------------
    def _remove_played_card(self, card):
        rank = int(card[:-1])
        suit = SUIT_MAP[card[-1]]

        if suit == Suit.DIAMONDS:
            assert rank in self._diamonds_cards
            self._diamonds_cards.remove(rank)
        elif suit == Suit.CLUBS:
            assert rank in self._clubs_cards
            self._clubs_cards.remove(rank)
        elif suit == Suit.HEARTS:
            assert rank in self._hearts_cards
            self._hearts_cards.remove(rank)
        elif suit == Suit.SPADES:
            assert rank in self._spades_cards
            self._spades_cards.remove(rank)

    def _add_undo_card(self, card):
        rank = int(card[:-1])
        suit = SUIT_MAP[card[-1]]

        if suit == Suit.DIAMONDS:
            if rank not in self._diamonds_cards:
                self._diamonds_cards.append(rank)
        elif suit == Suit.HEARTS:
            if rank not in self._hearts_cards:
                self._hearts_cards.append(rank)
        elif suit == Suit.CLUBS:
            if rank not in self._clubs_cards:
                self._clubs_cards.append(rank)
        elif suit == Suit.SPADES:
            if rank not in self._spades_cards:
                self._spades_cards.append(rank)

    # -------------------------------------------------------------------------
    # Play safe card logic
    # -------------------------------------------------------------------------
    def _play_safe_card(self, suit_id, match_played_cards):
        cards_on_floor = match_played_cards[:]
        legal_card_ranks = []
        for card in self._logic_holder.legal_cards:
            legal_card_ranks.append(int(card[:-1]))

        if self._suit_play_turns[suit_id] == 1:
            if self._is_suit_has_ace(legal_card_ranks):
                if (
                    self._is_suit_has_queen(legal_card_ranks)
                    and len(self._suit_cards[suit_id]) < 5
                ):
                    return "12" + self._get_suit_letter(suit_id)
                else:
                    return "14" + self._get_suit_letter(suit_id)
            else:
                if self._is_suit_has_king(self._suit_cards[suit_id]) or self._is_suit_has_queen(
                    self._suit_cards[suit_id]
                ):
                    if ("14" + self._get_suit_letter(suit_id)) not in match_played_cards:
                        return (
                            str(self._get_2nd_largest(legal_card_ranks))
                            + self._get_suit_letter(suit_id)
                        )

        if (
            self._suit_play_turns[suit_id] >= 2
            and self._get_largest_of_mycards_from_this_suit(suit_id)
            == self._get_senior_card(suit_id)
            and self._cut_trick_played[suit_id] == 0
        ):
            card = str(self._get_senior_card(suit_id)) + self._get_suit_letter(suit_id)
            cards_on_floor.append(card)
            if card == self._game_helper.get_winning_card(cards_on_floor):
                return card

        return self.get_confuser_card(
            int(self._get_smallest_from_legal_cards(match_played_cards)),
            suit_id,
        )

    # -------------------------------------------------------------------------
    # Play spades safely
    # -------------------------------------------------------------------------
    def _play_spades_safely(self, match_played_cards):
        card = ""
        legal_card_ranks = []
        cards_on_floor = match_played_cards[:]

        for each in self._logic_holder.legal_cards:
            legal_card_ranks.append(int(each[:-1]))

        if self._get_senior_card(Suit.SPADES) in legal_card_ranks:
            card = str(self._get_senior_card(Suit.SPADES)) + self._get_suit_letter(Suit.SPADES)
            cards_on_floor.append(card)
            if card == self._game_helper.get_winning_card(cards_on_floor):
                return card
            else:
                return self.get_confuser_card(
                    int(self._get_smallest_from_legal_cards(match_played_cards)),
                    Suit.SPADES,
                )
        else:
            largest = self._get_largest_of_mycards_from_this_suit(Suit.SPADES)
            for i in range(largest - 1, 1, -1):
                if i in legal_card_ranks and (i + 1) in legal_card_ranks:
                    continue
                if i in legal_card_ranks and (i + 1) not in legal_card_ranks:
                    card = str(i) + self._get_suit_letter(Suit.SPADES)
                    break
                card = str(self._get_2nd_largest(legal_card_ranks)) + self._get_suit_letter(Suit.SPADES)

            if card == "":
                card = str(self._get_2nd_largest(legal_card_ranks)) + self._get_suit_letter(Suit.SPADES)

            cards_on_floor.append(card)
            if card == self._game_helper.get_winning_card(cards_on_floor):
                return card
            else:
                return self.get_confuser_card(
                    int(self._get_smallest_from_legal_cards(match_played_cards)),
                    Suit.SPADES,
                )

    # -------------------------------------------------------------------------
    # Get non spades random card
    # -------------------------------------------------------------------------
    def _get_non_spades_random_card(self, non_spades_legal_cards, match_dealer):
        for suit in [Suit.DIAMONDS, Suit.CLUBS, Suit.HEARTS]:
            if (
                self._get_senior_card(suit) in self._suit_cards[suit]
                and self._cut_trick_played[suit] == 0
            ):
                if self._suit_play_turns[suit] < 2:
                    return str(self._get_senior_card(suit)) + self._get_suit_letter(suit)
                if self._suit_play_turns[suit] == 2 and self._logic_holder == self._game_helper.get_bid_player(
                    match_dealer, self._match_players, 3
                ):
                    total_bid = sum(p.bid for p in self._match_players)
                    if total_bid < 8:
                        return str(self._get_senior_card(suit)) + self._get_suit_letter(suit)

        return self._get_random_card(non_spades_legal_cards)

    # -------------------------------------------------------------------------
    # Throw turn logic
    # -------------------------------------------------------------------------
    def _get_card_from_first_throw_turn_logic(self, non_spades_legal_cards, match_dealer):
        card = ""

        # No spades remain with opponents
        if (
            len(self._unrevealed_cards[Suit.SPADES]) == len(self._suit_cards[Suit.SPADES])
            and len(self._suit_cards[Suit.SPADES]) > 0
        ):
            if self._logic_holder.bid - self._logic_holder.hands <= len(self._suit_cards[Suit.SPADES]):
                pass
            else:
                return str(max(self._suit_cards[Suit.SPADES])) + self._get_suit_letter(Suit.SPADES)

        # Only one spade remains with opponents
        if (
            len(self._unrevealed_cards[Suit.SPADES]) - len(self._suit_cards[Suit.SPADES]) == 1
            and len(self._suit_cards[Suit.SPADES]) > 0
            and self._get_senior_card(Suit.SPADES) in self._suit_cards[Suit.SPADES]
        ):
            return str(max(self._suit_cards[Suit.SPADES])) + self._get_suit_letter(Suit.SPADES)

        for suit in [Suit.DIAMONDS, Suit.CLUBS, Suit.HEARTS]:
            # Senior card and no spades remain
            if (
                len(self._suit_cards[suit]) > 0
                and self._get_senior_card(suit) in self._suit_cards[suit]
                and len(self._unrevealed_cards[Suit.SPADES]) == len(self._suit_cards[Suit.SPADES])
            ):
                return str(max(self._suit_cards[suit])) + self._get_suit_letter(suit)

            # Creating spades fight
            if (
                len(self._suit_cards[suit]) > 0
                and self._suit_play_turns[suit] > 2
                and self._get_senior_card(suit) not in self._suit_cards[suit]
                and len(self._unrevealed_cards[Suit.SPADES]) - len(self._suit_cards[Suit.SPADES]) >= 2
            ):
                return str(min(self._suit_cards[suit])) + self._get_suit_letter(suit)

        if self._logic_holder.hands >= self._logic_holder.bid:
            if len(self._suit_cards[Suit.SPADES]) > 0:
                return str(min(self._suit_cards[Suit.SPADES])) + self._get_suit_letter(Suit.SPADES)

            for suit in [Suit.DIAMONDS, Suit.CLUBS, Suit.HEARTS]:
                if (
                    self._cut_trick_played[suit] == 1
                    and len(self._suit_cards[suit]) > 0
                    and len(self._unrevealed_cards[Suit.SPADES]) - len(self._suit_cards[Suit.SPADES]) >= 2
                ):
                    return str(min(self._suit_cards[suit])) + self._get_suit_letter(suit)

        # Compete spades
        if (
            len(self._spades_cards) > 1
            and self._get_senior_card(Suit.SPADES) in self._spades_cards
            and len(self._unrevealed_cards[Suit.SPADES]) > 1
            and self._unrevealed_cards[Suit.SPADES][1] in self._spades_cards
        ):
            card = str(self._compete_spades()) + self._get_suit_letter(Suit.SPADES)
            # Remove COMPETE_SPADES strategy
            idx = 0
            for i in self._play_strategies:
                strategy = list(i.keys())[0]
                suit_v = list(i.values())[0]
                if strategy == Strategy.COMPETE_SPADES and suit_v == Suit.SPADES:
                    self._play_strategies.pop(idx)
                    break
                idx += 1
            return card

        if len(self._play_strategies) != 0:
            rand_num = random.randint(0, len(self._play_strategies) - 1)
            play_strategy = self._play_strategies[rand_num]

            for key in play_strategy:
                if len(self._suit_cards[play_strategy[key]]) != 0:
                    if len(self._suit_cards[play_strategy[key]]) == 1:
                        if key in [Strategy.PREPARE_FOR_CUT, Strategy.COMPETE_SPADES]:
                            card = self._get_play_strategy_card(play_strategy)
                        else:
                            if self._get_senior_card(play_strategy[key]) in self._suit_cards[play_strategy[key]]:
                                card = self._get_play_strategy_card(play_strategy)
                            elif self._get_senior_card(play_strategy[key]) - 1 in self._suit_cards[play_strategy[key]]:
                                if len(non_spades_legal_cards) != 0:
                                    card = self._get_non_spades_random_card(
                                        non_spades_legal_cards, match_dealer
                                    )
                            else:
                                card = self._get_play_strategy_card(play_strategy)
                    else:
                        card = self._get_play_strategy_card(play_strategy)
                else:
                    if len(non_spades_legal_cards) != 0:
                        card = self._get_non_spades_random_card(
                            non_spades_legal_cards, match_dealer
                        )
                        return card
                    card = str(self._compete_spades()) + self._get_suit_letter(Suit.SPADES)
                    # Remove compete_spades strategy
                    idx = 0
                    for i in self._play_strategies:
                        s = list(i.keys())[0]
                        sv = list(i.values())[0]
                        if s == Strategy.COMPETE_SPADES and sv == Suit.SPADES:
                            self._play_strategies.pop(idx)
                            break
                        idx += 1
                    return card
                self._play_strategies.pop(rand_num)
                return card
        else:
            for suit in [Suit.DIAMONDS, Suit.CLUBS, Suit.HEARTS]:
                if (
                    self._get_senior_card(suit) in self._suit_cards[suit]
                    and self._suit_play_turns[suit] == 0
                    and len(self._unrevealed_cards[suit]) < 13
                ):
                    return str(self._get_senior_card(suit)) + self._get_suit_letter(suit)

        if len(non_spades_legal_cards) != 0:
            return self._get_non_spades_random_card(non_spades_legal_cards, match_dealer)

        card = str(self._compete_spades()) + self._get_suit_letter(Suit.SPADES)
        idx = 0
        for i in self._play_strategies:
            s = list(i.keys())[0]
            sv = list(i.values())[0]
            if s == Strategy.COMPETE_SPADES and sv == Suit.SPADES:
                self._play_strategies.pop(idx)
                break
            idx += 1
        return card

    def _get_card_from_second_throw_turn_logic(self, match_played_cards):
        first_throw_card_suit = SUIT_MAP[match_played_cards[0][-1]]

        if first_throw_card_suit != Suit.SPADES:
            if len(self._suit_cards[first_throw_card_suit]) != 0:
                return self._play_safe_card(first_throw_card_suit, match_played_cards)
            else:
                if len(self._suit_cards[Suit.SPADES]) != 0:
                    return self.get_confuser_card(
                        int(self._get_smallest_from_legal_cards(match_played_cards)),
                        Suit.SPADES,
                    )
                else:
                    return self._on_cut_chance_but_have_no_spades(first_throw_card_suit)
        else:
            if len(self._suit_cards[Suit.SPADES]) != 0:
                return self._play_spades_safely(match_played_cards)
            else:
                return self._on_cut_chance_but_have_no_spades(first_throw_card_suit)

    def _get_card_from_third_throw_turn_logic(self, match_played_cards):
        first_throw_card_suit = SUIT_MAP[match_played_cards[0][-1]]
        cards_on_floor = match_played_cards[:]

        if first_throw_card_suit != Suit.SPADES:
            if len(self._suit_cards[first_throw_card_suit]) != 0:
                if SUIT_MAP[match_played_cards[1][-1]] != Suit.SPADES:
                    return self._play_safe_card(first_throw_card_suit, match_played_cards)
                else:
                    return self.get_confuser_card(
                        int(self._get_smallest_from_legal_cards(match_played_cards)),
                        first_throw_card_suit,
                    )
            else:
                if len(self._suit_cards[Suit.SPADES]) != 0:
                    cards_on_floor.append(
                        str(max(self._suit_cards[Suit.SPADES])) + "S"
                    )
                    if cards_on_floor[2] == self._game_helper.get_winning_card(cards_on_floor):
                        return self.get_confuser_card(
                            int(self._get_smallest_from_legal_cards(match_played_cards)),
                            Suit.SPADES,
                        )
                    else:
                        if self._is_all_spades_in_legal_cards():
                            return self.get_confuser_card(
                                int(self._get_smallest_from_legal_cards(match_played_cards)),
                                Suit.SPADES,
                            )
                        else:
                            return self._on_cut_chance_but_have_no_spades(first_throw_card_suit)
                else:
                    return self._on_cut_chance_but_have_no_spades(first_throw_card_suit)
        else:
            if len(self._suit_cards[Suit.SPADES]) != 0:
                return self._play_spades_safely(match_played_cards)
            else:
                return self._on_cut_chance_but_have_no_spades(first_throw_card_suit)

    def _get_card_from_last_throw_turn_logic(self, match_played_cards):
        card = ""
        first_throw_card_suit = SUIT_MAP[match_played_cards[0][-1]]

        if len(self._suit_cards[first_throw_card_suit]) == 0:
            if len(self._suit_cards[Suit.SPADES]) == 0:
                card = self._on_cut_chance_but_have_no_spades(first_throw_card_suit)
            elif self._is_all_spades_in_legal_cards():
                card = self.get_confuser_card(
                    int(self._get_smallest_from_legal_cards(match_played_cards)),
                    Suit.SPADES,
                )
            else:
                card = self._on_cut_chance_but_have_no_spades(first_throw_card_suit)
        else:
            card = self.get_confuser_card(
                int(self._get_smallest_from_legal_cards(match_played_cards)),
                first_throw_card_suit,
            )
        return card

    # -------------------------------------------------------------------------
    # Main throw card selection
    # -------------------------------------------------------------------------
    def select_throw_card(self, match_current_throw_turn, match_dealer, match_played_cards):
        """Select a card to throw based on current game state."""
        if len(self._logic_holder.legal_cards) == 1:
            selected_card = self._logic_holder.legal_cards[0]
        else:
            non_spades_legal_cards = []
            for card in self._logic_holder.legal_cards:
                if SUIT_MAP[card[-1]] != Suit.SPADES:
                    non_spades_legal_cards.append(card)

            if match_current_throw_turn == 0:
                selected_card = self._get_card_from_first_throw_turn_logic(
                    non_spades_legal_cards, match_dealer
                )
            elif match_current_throw_turn == 1:
                selected_card = self._get_card_from_second_throw_turn_logic(
                    match_played_cards
                )
            elif match_current_throw_turn == 2:
                selected_card = self._get_card_from_third_throw_turn_logic(
                    match_played_cards
                )
            elif match_current_throw_turn == 3:
                selected_card = self._get_card_from_last_throw_turn_logic(
                    match_played_cards
                )
            elif len(non_spades_legal_cards) != 0:
                selected_card = self._get_non_spades_random_card(
                    non_spades_legal_cards, match_dealer
                )
            else:
                selected_card = self._get_random_card(self._logic_holder.legal_cards)

        assert selected_card in self._logic_holder.legal_cards, (
            f"Selected card {selected_card} not in legal cards {self._logic_holder.legal_cards}"
        )
        return selected_card

    # -------------------------------------------------------------------------
    # Game event handlers
    # -------------------------------------------------------------------------
    def on_throw_turn_started(self, game_round, play_turn, throw_turn, throw_player):
        if throw_turn == 0:
            if play_turn == 0:
                self._game_starter = throw_player
                self._create_play_strategies()

    def on_throw_card_selected(self, card, game_round, play_turn, throw_turn, thrown_by):
        if thrown_by == self._logic_holder:
            self._remove_played_card(card)

        self._unrevealed_cards[SUIT_MAP[card[-1]]].remove(int(card[:-1]))

        if throw_turn == 0:
            self._suit_play_turns[SUIT_MAP[card[-1]]] += 1

    def on_throw_turn_completed(self, throw_turn, thrown_by, match_played_cards):
        if throw_turn == 3:
            first_throw_card_suit = SUIT_MAP[match_played_cards[0][-1]]
            winning_card_suit = SUIT_MAP[
                self._game_helper.get_winning_card(match_played_cards)[-1]
            ]
            if first_throw_card_suit != Suit.SPADES:
                if winning_card_suit == Suit.SPADES:
                    self._cut_trick_played[first_throw_card_suit] = 1

            # Find play turn starter
            play_turn_starter = self._game_helper.get_right_side_player(
                thrown_by, self._match_players
            )

            if self._logic_holder != play_turn_starter:
                idx = 0
                for i in self._play_strategies:
                    strategy = list(i.keys())[0]
                    suit_val = list(i.values())[0]
                    if strategy == Strategy.BRING_DOWN_ACE and suit_val == first_throw_card_suit:
                        self._play_strategies.pop(idx)
                    if strategy == Strategy.BRING_DOWN_KING and suit_val == first_throw_card_suit:
                        self._play_strategies.pop(idx)
                    if strategy == Strategy.COMPETE_SPADES and suit_val == first_throw_card_suit:
                        self._play_strategies.pop(idx)
                        break
                    idx += 1

    def on_game_round_completed(self, round_num):
        self._clear_bot_logic_data()
