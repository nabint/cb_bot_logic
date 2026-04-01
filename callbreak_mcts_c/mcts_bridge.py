"""
mcts_bridge.py — Python ctypes wrapper for the C MCTS engine.

Translates between Python string cards ('14S', '2D') and C uint8_t encoding.
Provides the same mcts_search() API as callbreak_mcts_node.py so main.py
can switch between Python and C engines seamlessly.
"""

import ctypes
import os
import sys
import platform

# ---- Load shared library ----
_dir = os.path.dirname(os.path.abspath(__file__))
if platform.system() == "Darwin":
    _lib_name = "mcts_engine.dylib"
else:
    _lib_name = "mcts_engine.so"

_lib_path = os.path.join(_dir, _lib_name)
if not os.path.exists(_lib_path):
    raise RuntimeError(
        f"C library not found at {_lib_path}. "
        f"Run 'make' in {_dir} first."
    )

_lib = ctypes.CDLL(_lib_path)

# ---- Constants ----
NUM_PLAYERS = 4
CARDS_PER_HAND = 13
TOTAL_CARDS = 52
MAX_LEGAL = 13

SUIT_DIAMONDS = 0
SUIT_CLUBS = 1
SUIT_HEARTS = 2
SUIT_SPADES = 3

# Map string suit chars to C suit IDs
_SUIT_CHAR_TO_ID = {"D": SUIT_DIAMONDS, "C": SUIT_CLUBS, "H": SUIT_HEARTS, "S": SUIT_SPADES}
_SUIT_ID_TO_CHAR = {v: k for k, v in _SUIT_CHAR_TO_ID.items()}

# Also map bid_logic.Suit enum values
sys.path.insert(0, os.path.join(_dir, ".."))
try:
    from bid_logic import Suit
    _SUIT_ENUM_TO_ID = {
        Suit.DIAMONDS: SUIT_DIAMONDS,
        Suit.CLUBS:    SUIT_CLUBS,
        Suit.HEARTS:   SUIT_HEARTS,
        Suit.SPADES:   SUIT_SPADES,
    }
except ImportError:
    _SUIT_ENUM_TO_ID = {}


# ---- Card encoding/decoding ----
def encode_card(card_str):
    """Convert '14S' → uint8_t = (rank << 2) | suit"""
    suit_char = card_str[-1]
    rank = int(card_str[:-1])
    return (rank << 2) | _SUIT_CHAR_TO_ID[suit_char]


def decode_card(card_byte):
    """Convert uint8_t → '14S'"""
    rank = card_byte >> 2
    suit = card_byte & 3
    return f"{rank}{_SUIT_ID_TO_CHAR[suit]}"


def encode_cards(card_strs):
    """Convert list of string cards to ctypes uint8 array."""
    n = len(card_strs)
    arr = (ctypes.c_uint8 * n)()
    for i, c in enumerate(card_strs):
        arr[i] = encode_card(c)
    return arr, n


def encode_led_suit(led_suit):
    """Convert Python led_suit (Suit enum or None) to C int (-1 for none)."""
    if led_suit is None:
        return -1
    if isinstance(led_suit, int) and led_suit in _SUIT_ENUM_TO_ID:
        return _SUIT_ENUM_TO_ID[led_suit]
    if hasattr(led_suit, 'value'):
        return _SUIT_ENUM_TO_ID.get(led_suit, -1)
    return -1


def encode_void_tracker(void_tracker):
    """Convert list of 4 sets/lists of Suit enums to 4-byte bitmask array."""
    arr = (ctypes.c_uint8 * NUM_PLAYERS)()
    for i in range(NUM_PLAYERS):
        mask = 0
        for suit in void_tracker[i]:
            if isinstance(suit, int):
                sid = _SUIT_ENUM_TO_ID.get(suit, -1)
            else:
                sid = _SUIT_ENUM_TO_ID.get(suit, -1)
            if sid >= 0:
                mask |= (1 << sid)
        arr[i] = mask
    return arr


# ---- C struct definitions ----
class ActionStat(ctypes.Structure):
    _fields_ = [
        ("card", ctypes.c_uint8),
        ("visits", ctypes.c_int),
        ("total_reward", ctypes.c_double),
        ("avg", ctypes.c_double),
    ]


class SearchParams(ctypes.Structure):
    _fields_ = [
        ("iterations", ctypes.c_int),
        ("sims_per_det", ctypes.c_int),
        ("block_leader", ctypes.c_int),
        ("cumulative_scores", ctypes.c_double * NUM_PLAYERS),
        ("time_limit_ms", ctypes.c_int),
        ("human_index", ctypes.c_int),
        ("current_round", ctypes.c_int),
        ("total_rounds", ctypes.c_int),
        ("player_in_game_ids", ctypes.c_int * NUM_PLAYERS),
    ]


class SearchResult(ctypes.Structure):
    _fields_ = [
        ("best_card", ctypes.c_uint8),
        ("num_actions", ctypes.c_int),
        ("actions", ActionStat * MAX_LEGAL),
    ]


# ---- Set up function signature ----
_lib.mcts_search_c.argtypes = [
    ctypes.POINTER(ctypes.c_uint8),    # original_deck
    ctypes.POINTER(ctypes.c_uint8),    # known_hand
    ctypes.c_int,                       # known_hand_size
    ctypes.POINTER(ctypes.c_int),      # bids
    ctypes.POINTER(ctypes.c_int),      # tricks_won
    ctypes.c_int,                       # current_turn
    ctypes.POINTER(ctypes.c_uint8),    # cards_played
    ctypes.c_int,                       # cards_played_count
    ctypes.c_int,                       # trick_starter
    ctypes.c_int,                       # dealer_index
    ctypes.POINTER(ctypes.c_uint8),    # discard_pile
    ctypes.c_int,                       # discard_count
    ctypes.c_int,                       # led_suit
    ctypes.POINTER(ctypes.c_uint8),    # void_tracker
    ctypes.POINTER(ctypes.c_int),      # discard_starters
    ctypes.c_int,                       # discard_trick_count
    ctypes.c_int,                       # player_index
    ctypes.POINTER(SearchParams),      # params
    ctypes.POINTER(SearchResult),      # result
]
_lib.mcts_search_c.restype = None

_lib.bot_logic_select_card_c.argtypes = [
    ctypes.POINTER(ctypes.c_uint8),    # known_hand
    ctypes.c_int,                       # known_hand_size
    ctypes.POINTER(ctypes.c_int),      # bids
    ctypes.POINTER(ctypes.c_int),      # tricks_won
    ctypes.c_int,                       # current_turn
    ctypes.POINTER(ctypes.c_uint8),    # cards_played
    ctypes.c_int,                       # cards_played_count
    ctypes.c_int,                       # trick_starter
    ctypes.c_int,                       # dealer_index
    ctypes.POINTER(ctypes.c_uint8),    # discard_pile
    ctypes.c_int,                       # discard_count
    ctypes.POINTER(ctypes.c_int),      # discard_starters
    ctypes.c_int,                       # discard_trick_count
    ctypes.c_int,                       # led_suit
    ctypes.c_int,                       # player_index
]
_lib.bot_logic_select_card_c.restype = ctypes.c_uint8

_lib.get_legal_cards_c.argtypes = [
    ctypes.POINTER(ctypes.c_uint8),
    ctypes.c_int,
    ctypes.POINTER(ctypes.c_uint8),
    ctypes.c_int,
    ctypes.c_int,
    ctypes.POINTER(ctypes.c_uint8),
]
_lib.get_legal_cards_c.restype = ctypes.c_int

_lib.determinize_hidden_hands_c.argtypes = [
    ctypes.POINTER(ctypes.c_uint8),    # original_deck
    ctypes.POINTER(ctypes.c_uint8),    # known_hand
    ctypes.c_int,                       # known_hand_size
    ctypes.POINTER(ctypes.c_int),      # bids
    ctypes.POINTER(ctypes.c_int),      # tricks_won
    ctypes.c_int,                       # current_turn
    ctypes.POINTER(ctypes.c_uint8),    # cards_played
    ctypes.c_int,                       # cards_played_count
    ctypes.c_int,                       # trick_starter
    ctypes.c_int,                       # dealer_index
    ctypes.POINTER(ctypes.c_uint8),    # discard_pile
    ctypes.c_int,                       # discard_count
    ctypes.c_int,                       # led_suit
    ctypes.POINTER(ctypes.c_uint8),    # void_tracker
    ctypes.POINTER(ctypes.c_int),      # discard_starters
    ctypes.c_int,                       # discard_trick_count
    ctypes.c_int,                       # player_index
    ctypes.POINTER(ctypes.c_int),      # player_in_game_ids
    ctypes.POINTER(ctypes.c_uint8),    # out_hands
    ctypes.POINTER(ctypes.c_int),      # out_hand_sizes
]
_lib.determinize_hidden_hands_c.restype = None

_lib.debug_inference_snapshot_c.argtypes = [
    ctypes.POINTER(ctypes.c_uint8),    # known_hand
    ctypes.c_int,                       # known_hand_size
    ctypes.POINTER(ctypes.c_int),      # tricks_won
    ctypes.POINTER(ctypes.c_uint8),    # cards_played
    ctypes.c_int,                       # cards_played_count
    ctypes.c_int,                       # trick_starter
    ctypes.c_int,                       # dealer_index
    ctypes.POINTER(ctypes.c_uint8),    # discard_pile
    ctypes.c_int,                       # discard_count
    ctypes.POINTER(ctypes.c_uint8),    # void_tracker
    ctypes.POINTER(ctypes.c_int),      # discard_starters
    ctypes.c_int,                       # discard_trick_count
    ctypes.c_int,                       # player_index
    ctypes.POINTER(ctypes.c_int),      # player_in_game_ids
    ctypes.POINTER(ctypes.c_uint8),    # out_voids
    ctypes.POINTER(ctypes.c_int),      # out_max_ranks
]
_lib.debug_inference_snapshot_c.restype = None


# ---- Public Python API ----
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
    dealer_index=0,
    player_index=0,
    iterations=200,
    simulations_per_det=10,
    time_limit_ms=None,
    block_leader=False,
    cumulative_scores=None,
    human_index=-1,
    current_round=1,
    total_rounds=5,
    discard_starters=None,
    player_in_game_ids=None,
):
    """
    Run MCTS search via C engine.
    Same API as callbreak_mcts_node.mcts_search().
    Returns: (best_card_str, action_stats_dict)
    """
    # Encode inputs
    c_deck, _ = encode_cards(original_deck)
    c_hand, hand_size = encode_cards(known_hand)
    c_bids = (ctypes.c_int * NUM_PLAYERS)(*bids)
    c_tricks = (ctypes.c_int * NUM_PLAYERS)(*tricks_won)

    if cards_played:
        c_played, played_count = encode_cards(cards_played)
    else:
        c_played = (ctypes.c_uint8 * 0)()
        played_count = 0

    if discard_pile:
        c_discard, discard_count = encode_cards(discard_pile)
    else:
        c_discard = (ctypes.c_uint8 * 0)()
        discard_count = 0

    c_led_suit = encode_led_suit(led_suit)
    c_void = encode_void_tracker(void_tracker)

    # Search params
    params = SearchParams()
    params.iterations = iterations
    params.sims_per_det = simulations_per_det
    params.block_leader = 1 if block_leader else 0
    params.time_limit_ms = time_limit_ms or 0
    params.human_index = human_index
    params.current_round = current_round
    params.total_rounds = total_rounds

    if cumulative_scores:
        for i in range(NUM_PLAYERS):
            params.cumulative_scores[i] = cumulative_scores[i]

    # Default player_in_game_ids to seat order unless caller provides stable IDs.
    if player_in_game_ids is None:
        player_in_game_ids = list(range(NUM_PLAYERS))
    for i in range(NUM_PLAYERS):
        params.player_in_game_ids[i] = player_in_game_ids[i]

    # Encode discard starters
    if discard_starters:
        trick_count = len(discard_starters)
        c_starters = (ctypes.c_int * trick_count)(*discard_starters)
    else:
        c_starters = (ctypes.c_int * 0)()
        trick_count = 0

    # Call C engine
    result = SearchResult()
    _lib.mcts_search_c(
        c_deck, c_hand, hand_size,
        c_bids, c_tricks, current_turn,
        c_played, played_count,
        trick_starter, dealer_index,
        c_discard, discard_count,
        c_led_suit, c_void,
        c_starters, trick_count,
        player_index,
        ctypes.byref(params),
        ctypes.byref(result),
    )

    # Decode result
    best_card = decode_card(result.best_card)
    action_stats = {}
    for i in range(result.num_actions):
        a = result.actions[i]
        card_str = decode_card(a.card)
        action_stats[card_str] = {
            "v": a.visits,
            "w": a.total_reward,
            "avg": a.avg,
        }

    return best_card, action_stats


def bot_logic_select_card(
    known_hand,
    bids,
    tricks_won,
    current_turn,
    cards_played,
    trick_starter,
    discard_pile,
    led_suit,
    dealer_index=0,
    player_index=0,
    discard_starters=None,
):
    """
    Calls the C equivalent of Python BotLogic.select_throw_card.
    Reconstructs the trick state internally in the C engine.
    """
    c_hand, hand_size = encode_cards(known_hand)
    c_bids = (ctypes.c_int * NUM_PLAYERS)(*bids)
    c_tricks = (ctypes.c_int * NUM_PLAYERS)(*tricks_won)

    if cards_played:
        c_played, played_count = encode_cards(cards_played)
    else:
        c_played = (ctypes.c_uint8 * 0)()
        played_count = 0

    if discard_pile:
        c_discard, discard_count = encode_cards(discard_pile)
    else:
        c_discard = (ctypes.c_uint8 * 0)()
        discard_count = 0

    c_led_suit = encode_led_suit(led_suit)

    # Encode discard starters
    if discard_starters:
        trick_count = len(discard_starters)
        c_starters = (ctypes.c_int * trick_count)(*discard_starters)
    else:
        c_starters = (ctypes.c_int * 0)()
        trick_count = 0

    # Call C engine
    result_card = _lib.bot_logic_select_card_c(
        c_hand, hand_size, c_bids, c_tricks,
        current_turn, c_played, played_count,
        trick_starter, dealer_index,
        c_discard, discard_count,
        c_starters, trick_count,
        c_led_suit, player_index
    )

    return decode_card(result_card)


def get_legal_cards_c(hand, played_cards, led_suit):
    c_hand, hand_size = encode_cards(hand)
    if played_cards:
        c_played, played_count = encode_cards(played_cards)
    else:
        c_played = (ctypes.c_uint8 * 0)()
        played_count = 0

    c_led_suit = encode_led_suit(led_suit)
    out = (ctypes.c_uint8 * MAX_LEGAL)()
    count = _lib.get_legal_cards_c(
        c_hand, hand_size,
        c_played, played_count,
        c_led_suit, out,
    )
    return [decode_card(out[i]) for i in range(count)]


def determinize_hidden_hands(
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
    dealer_index=0,
    player_index=0,
    discard_starters=None,
    player_in_game_ids=None,
):
    c_deck, _ = encode_cards(original_deck)
    c_hand, hand_size = encode_cards(known_hand)
    c_bids = (ctypes.c_int * NUM_PLAYERS)(*bids)
    c_tricks = (ctypes.c_int * NUM_PLAYERS)(*tricks_won)

    if cards_played:
        c_played, played_count = encode_cards(cards_played)
    else:
        c_played = (ctypes.c_uint8 * 0)()
        played_count = 0

    if discard_pile:
        c_discard, discard_count = encode_cards(discard_pile)
    else:
        c_discard = (ctypes.c_uint8 * 0)()
        discard_count = 0

    c_led_suit = encode_led_suit(led_suit)
    c_void = encode_void_tracker(void_tracker)

    if discard_starters:
        trick_count = len(discard_starters)
        c_starters = (ctypes.c_int * trick_count)(*discard_starters)
    else:
        c_starters = (ctypes.c_int * 0)()
        trick_count = 0

    if player_in_game_ids is None:
        player_in_game_ids = list(range(NUM_PLAYERS))
    c_player_ids = (ctypes.c_int * NUM_PLAYERS)(*player_in_game_ids)

    out_hands = (ctypes.c_uint8 * (NUM_PLAYERS * CARDS_PER_HAND))()
    out_sizes = (ctypes.c_int * NUM_PLAYERS)()
    _lib.determinize_hidden_hands_c(
        c_deck, c_hand, hand_size,
        c_bids, c_tricks,
        current_turn,
        c_played, played_count,
        trick_starter, dealer_index,
        c_discard, discard_count,
        c_led_suit, c_void,
        c_starters, trick_count,
        player_index, c_player_ids,
        out_hands, out_sizes,
    )

    decoded = []
    for i in range(NUM_PLAYERS):
        size = out_sizes[i]
        hand = [decode_card(out_hands[i * CARDS_PER_HAND + j]) for j in range(size)]
        decoded.append(hand)
    return decoded


def debug_inference_snapshot(
    known_hand,
    tricks_won,
    cards_played,
    trick_starter,
    discard_pile,
    void_tracker,
    dealer_index=0,
    player_index=0,
    discard_starters=None,
    player_in_game_ids=None,
):
    c_hand, hand_size = encode_cards(known_hand)
    c_tricks = (ctypes.c_int * NUM_PLAYERS)(*tricks_won)

    if cards_played:
        c_played, played_count = encode_cards(cards_played)
    else:
        c_played = (ctypes.c_uint8 * 0)()
        played_count = 0

    if discard_pile:
        c_discard, discard_count = encode_cards(discard_pile)
    else:
        c_discard = (ctypes.c_uint8 * 0)()
        discard_count = 0

    c_void = encode_void_tracker(void_tracker)

    if discard_starters:
        trick_count = len(discard_starters)
        c_starters = (ctypes.c_int * trick_count)(*discard_starters)
    else:
        c_starters = (ctypes.c_int * 0)()
        trick_count = 0

    if player_in_game_ids is None:
        player_in_game_ids = list(range(NUM_PLAYERS))
    c_player_ids = (ctypes.c_int * NUM_PLAYERS)(*player_in_game_ids)

    out_voids = (ctypes.c_uint8 * NUM_PLAYERS)()
    out_max = (ctypes.c_int * (NUM_PLAYERS * 4))()
    _lib.debug_inference_snapshot_c(
        c_hand, hand_size,
        c_tricks,
        c_played, played_count,
        trick_starter, dealer_index,
        c_discard, discard_count,
        c_void,
        c_starters, trick_count,
        player_index, c_player_ids,
        out_voids, out_max,
    )

    max_ranks = []
    for i in range(NUM_PLAYERS):
        max_ranks.append([out_max[i * 4 + s] for s in range(4)])
    return {
        "voids": [out_voids[i] for i in range(NUM_PLAYERS)],
        "max_ranks": max_ranks,
    }
