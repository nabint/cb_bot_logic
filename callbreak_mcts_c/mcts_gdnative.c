/*
 * mcts_gdnative.c — GDNative C wrapper for the Callbreak MCTS engine.
 *
 * Exposes a NativeScript class "MCTSEngine" with a single method:
 *   mcts_search(args: Dictionary) -> Dictionary
 *
 * This mirrors the Python mcts_bridge.py API so GDScript can call
 * the C MCTS engine directly in Godot 3.6.
 *
 * Build: make gdnative
 */

#include <stdlib.h>
#include <string.h>
#include <stdio.h>

#include "gdnative_api_struct.gen.h"
#include "mcts_engine.h"
#include "bot_logic_c.h"

/* ======================================================================
 * GDNative API pointers
 * ====================================================================== */
static const godot_gdnative_core_api_struct *api = NULL;
static const godot_gdnative_ext_nativescript_api_struct *nativescript_api = NULL;

/* ======================================================================
 * Helper: create a godot_string from a C string
 * ====================================================================== */
static godot_string gd_string_from_cstr(const char *cstr) {
    godot_string s;
    api->godot_string_new(&s);
    api->godot_string_parse_utf8(&s, cstr);
    return s;
}

/* ======================================================================
 * Helper: extract C string from godot_string (caller must destroy cs)
 * ====================================================================== */
static const char *gd_string_to_cstr(const godot_string *gds, godot_char_string *cs) {
    *cs = api->godot_string_utf8(gds);
    return api->godot_char_string_get_data(cs);
}

/* ======================================================================
 * Helper: get a value from a dictionary by C string key
 * ====================================================================== */
static godot_variant dict_get(const godot_dictionary *dict, const char *key) {
    godot_string gd_key = gd_string_from_cstr(key);
    godot_variant var_key;
    api->godot_variant_new_string(&var_key, &gd_key);
    godot_variant result = api->godot_dictionary_get(dict, &var_key);
    api->godot_variant_destroy(&var_key);
    api->godot_string_destroy(&gd_key);
    return result;
}

/* ======================================================================
 * Helper: check if dictionary has a key
 * ====================================================================== */
static godot_bool dict_has(const godot_dictionary *dict, const char *key) {
    godot_string gd_key = gd_string_from_cstr(key);
    godot_variant var_key;
    api->godot_variant_new_string(&var_key, &gd_key);
    godot_bool result = api->godot_dictionary_has(dict, &var_key);
    api->godot_variant_destroy(&var_key);
    api->godot_string_destroy(&gd_key);
    return result;
}

/* ======================================================================
 * Helper: set a value in a dictionary by C string key
 * ====================================================================== */
static void dict_set(godot_dictionary *dict, const char *key, const godot_variant *value) {
    godot_string gd_key = gd_string_from_cstr(key);
    godot_variant var_key;
    api->godot_variant_new_string(&var_key, &gd_key);
    api->godot_dictionary_set(dict, &var_key, value);
    api->godot_variant_destroy(&var_key);
    api->godot_string_destroy(&gd_key);
}

/* ======================================================================
 * Card encoding: string card "14S" -> uint8_t = (rank << 2) | suit
 * Suit mapping: D=0, C=1, H=2, S=3
 * ====================================================================== */
static int suit_char_to_id(char c) {
    switch (c) {
        case 'D': case 'd': return SUIT_DIAMONDS;
        case 'C': case 'c': return SUIT_CLUBS;
        case 'H': case 'h': return SUIT_HEARTS;
        case 'S': case 's': return SUIT_SPADES;
        default: return -1;
    }
}

static char suit_id_to_char(int suit) {
    switch (suit) {
        case SUIT_DIAMONDS: return 'D';
        case SUIT_CLUBS:    return 'C';
        case SUIT_HEARTS:   return 'H';
        case SUIT_SPADES:   return 'S';
        default: return '?';
    }
}

static uint8_t encode_card_str(const char *card_str) {
    /* Parse rank (everything before last char) and suit (last char) */
    int len = (int)strlen(card_str);
    if (len < 2) return CARD_NONE;
    char suit_char = card_str[len - 1];
    int suit = suit_char_to_id(suit_char);
    if (suit < 0) return CARD_NONE;

    /* Parse rank from the digits before the suit char */
    int rank = 0;
    for (int i = 0; i < len - 1; i++) {
        rank = rank * 10 + (card_str[i] - '0');
    }
    return MAKE_CARD(rank, suit);
}

/* Decode uint8_t card to string like "14S". buf must be >= 4 bytes. */
static void decode_card_to_str(uint8_t card, char *buf) {
    int rank = CARD_RANK(card);
    int suit = CARD_SUIT(card);
    snprintf(buf, 5, "%d%c", rank, suit_id_to_char(suit));
}

static int get_winning_index_local(const uint8_t *cards, int count) {
    int led_suit = CARD_SUIT(cards[0]);
    int best_rank = CARD_RANK(cards[0]);
    int best_suit = led_suit;
    int best_idx = 0;

    for (int i = 1; i < count; i++) {
        int suit = CARD_SUIT(cards[i]);
        int rank = CARD_RANK(cards[i]);
        if (suit == SUIT_SPADES && best_suit != SUIT_SPADES) {
            best_rank = rank;
            best_suit = suit;
            best_idx = i;
        } else if (suit == best_suit && rank > best_rank) {
            best_rank = rank;
            best_idx = i;
        }
    }
    return best_idx;
}

/* ======================================================================
 * Helper: convert a GDScript Array of card strings to C uint8 array
 * Returns count of cards. out must be large enough.
 * ====================================================================== */
static int encode_card_array(const godot_array *arr, uint8_t *out, int max_count) {
    int n = api->godot_array_size(arr);
    if (n > max_count) n = max_count;
    for (int i = 0; i < n; i++) {
        godot_variant elem = api->godot_array_get(arr, i);
        godot_string gs = api->godot_variant_as_string(&elem);
        godot_char_string cs;
        const char *cstr = gd_string_to_cstr(&gs, &cs);
        out[i] = encode_card_str(cstr);
        api->godot_char_string_destroy(&cs);
        api->godot_string_destroy(&gs);
        api->godot_variant_destroy(&elem);
    }
    return n;
}

/* ======================================================================
 * Helper: convert GDScript Array of ints to C int array
 * ====================================================================== */
static int decode_int_array(const godot_array *arr, int *out, int max_count) {
    int n = api->godot_array_size(arr);
    if (n > max_count) n = max_count;
    for (int i = 0; i < n; i++) {
        godot_variant elem = api->godot_array_get(arr, i);
        out[i] = (int)api->godot_variant_as_int(&elem);
        api->godot_variant_destroy(&elem);
    }
    return n;
}

/* ======================================================================
 * Helper: convert GDScript Array of floats/doubles to C double array
 * ====================================================================== */
static int decode_double_array(const godot_array *arr, double *out, int max_count) {
    int n = api->godot_array_size(arr);
    if (n > max_count) n = max_count;
    for (int i = 0; i < n; i++) {
        godot_variant elem = api->godot_array_get(arr, i);
        out[i] = api->godot_variant_as_real(&elem);
        api->godot_variant_destroy(&elem);
    }
    return n;
}

/* ======================================================================
 * Helper: encode void_tracker from Array of Arrays of suit ints
 * void_tracker[player] = Array of suit ints that player is void in
 * Output: 4-byte bitmask array, bit 0=D, 1=C, 2=H, 3=S
 * ====================================================================== */
static void encode_void_tracker(const godot_array *vt_arr, uint8_t *out) {
    for (int i = 0; i < NUM_PLAYERS; i++) {
        out[i] = 0;
    }
    int n = api->godot_array_size(vt_arr);
    if (n > NUM_PLAYERS) n = NUM_PLAYERS;
    for (int i = 0; i < n; i++) {
        godot_variant player_var = api->godot_array_get(vt_arr, i);
        godot_array player_arr = api->godot_variant_as_array(&player_var);
        int suit_count = api->godot_array_size(&player_arr);
        uint8_t mask = 0;
        for (int j = 0; j < suit_count; j++) {
            godot_variant suit_var = api->godot_array_get(&player_arr, j);
            int suit_id = (int)api->godot_variant_as_int(&suit_var);
            if (suit_id >= 0 && suit_id < NUM_SUITS) {
                mask |= VOID_BIT(suit_id);
            }
            api->godot_variant_destroy(&suit_var);
        }
        out[i] = mask;
        api->godot_array_destroy(&player_arr);
        api->godot_variant_destroy(&player_var);
    }
}

/* ======================================================================
 * NativeScript class: MCTSEngine
 * ====================================================================== */

/* Instance data (nothing needed, stateless) */
typedef struct {
    int _unused;
} MCTSEngineData;

/* ======================================================================
 * NativeScript class: BotLogicEngine
 * ====================================================================== */

/* Instance data (nothing needed, stateless) */
typedef struct {
    int _unused;
} BotLogicEngineData;

/* Constructor */
static void *mcts_engine_constructor(godot_object *p_instance, void *p_method_data) {
    (void)p_instance;
    (void)p_method_data;
    MCTSEngineData *data = (MCTSEngineData *)api->godot_alloc(sizeof(MCTSEngineData));
    data->_unused = 0;
    return data;
}

static void *bot_logic_engine_constructor(godot_object *p_instance, void *p_method_data) {
    (void)p_instance;
    (void)p_method_data;
    BotLogicEngineData *data = (BotLogicEngineData *)api->godot_alloc(sizeof(BotLogicEngineData));
    data->_unused = 0;
    return data;
}

/* Destructor */
static void mcts_engine_destructor(godot_object *p_instance, void *p_method_data, void *p_user_data) {
    (void)p_instance;
    (void)p_method_data;
    api->godot_free(p_user_data);
}

static void bot_logic_engine_destructor(godot_object *p_instance, void *p_method_data, void *p_user_data) {
    (void)p_instance;
    (void)p_method_data;
    api->godot_free(p_user_data);
}

/* ======================================================================
 * mcts_search(args_dict: Dictionary) -> Dictionary
 *
 * Input keys (all required unless noted):
 *   "original_deck"    : Array of String (card strings like "14S")
 *   "known_hand"       : Array of String
 *   "bids"             : Array of int (4 elements)
 *   "tricks_won"       : Array of int (4 elements)
 *   "current_turn"     : int
 *   "cards_played"     : Array of String (current trick, may be empty)
 *   "trick_starter"    : int
 *   "dealer_index"     : int (optional, default 0)
 *   "discard_pile"     : Array of String (may be empty)
 *   "led_suit"         : int (-1 for none, 0=D, 1=C, 2=H, 3=S)
 *   "void_tracker"     : Array of Array of int
 *   "player_index"     : int (optional, default 0)
 *   "iterations"       : int (optional, default 200)
 *   "sims_per_det"     : int (optional, default 10)
 *   "time_limit_ms"    : int (optional, default 0)
 *   "block_leader"     : bool (optional, default false)
 *   "cumulative_scores": Array of float (optional, 4 elements)
 *   "current_round"    : int (optional, default 1)
 *   "total_rounds"     : int (optional, default 5)
 *
 * Returns:
 *   {"best_card": String, "actions": Dictionary}
 *   where actions[card_string] = {"v": int, "w": float, "avg": float}
 * ====================================================================== */
static godot_variant mcts_search_method(godot_object *p_instance, void *p_method_data,
                                         void *p_user_data, int p_num_args,
                                         godot_variant **p_args) {
    (void)p_instance;
    (void)p_method_data;
    (void)p_user_data;

    godot_variant ret;
    api->godot_variant_new_nil(&ret);

    if (p_num_args < 1) {
        return ret;
    }

    /* Get the input dictionary */
    godot_dictionary args_dict = api->godot_variant_as_dictionary(p_args[0]);

    /* -- Decode original_deck -- */
    uint8_t c_deck[TOTAL_CARDS];
    int deck_size = 0;
    {
        godot_variant v = dict_get(&args_dict, "original_deck");
        godot_array arr = api->godot_variant_as_array(&v);
        deck_size = encode_card_array(&arr, c_deck, TOTAL_CARDS);
        api->godot_array_destroy(&arr);
        api->godot_variant_destroy(&v);
    }

    /* -- Decode known_hand -- */
    uint8_t c_hand[CARDS_PER_HAND];
    int hand_size = 0;
    {
        godot_variant v = dict_get(&args_dict, "known_hand");
        godot_array arr = api->godot_variant_as_array(&v);
        hand_size = encode_card_array(&arr, c_hand, CARDS_PER_HAND);
        api->godot_array_destroy(&arr);
        api->godot_variant_destroy(&v);
    }

    /* -- Decode bids -- */
    int c_bids[NUM_PLAYERS] = {0};
    {
        godot_variant v = dict_get(&args_dict, "bids");
        godot_array arr = api->godot_variant_as_array(&v);
        decode_int_array(&arr, c_bids, NUM_PLAYERS);
        api->godot_array_destroy(&arr);
        api->godot_variant_destroy(&v);
    }

    /* -- Decode tricks_won -- */
    int c_tricks_won[NUM_PLAYERS] = {0};
    {
        godot_variant v = dict_get(&args_dict, "tricks_won");
        godot_array arr = api->godot_variant_as_array(&v);
        decode_int_array(&arr, c_tricks_won, NUM_PLAYERS);
        api->godot_array_destroy(&arr);
        api->godot_variant_destroy(&v);
    }

    /* -- Decode current_turn -- */
    int current_turn = 0;
    {
        godot_variant v = dict_get(&args_dict, "current_turn");
        current_turn = (int)api->godot_variant_as_int(&v);
        api->godot_variant_destroy(&v);
    }

    /* -- Decode cards_played (current trick) -- */
    uint8_t c_played[NUM_PLAYERS];
    int played_count = 0;
    {
        godot_variant v = dict_get(&args_dict, "cards_played");
        godot_array arr = api->godot_variant_as_array(&v);
        played_count = encode_card_array(&arr, c_played, NUM_PLAYERS);
        api->godot_array_destroy(&arr);
        api->godot_variant_destroy(&v);
    }

    /* -- Decode trick_starter -- */
    int trick_starter = 0;
    {
        godot_variant v = dict_get(&args_dict, "trick_starter");
        trick_starter = (int)api->godot_variant_as_int(&v);
        api->godot_variant_destroy(&v);
    }

    /* -- Decode dealer_index (optional, default 0) -- */
    int dealer_index = 0;
    if (dict_has(&args_dict, "dealer_index")) {
        godot_variant v = dict_get(&args_dict, "dealer_index");
        dealer_index = (int)api->godot_variant_as_int(&v);
        api->godot_variant_destroy(&v);
    }

    /* -- Decode discard_pile -- */
    uint8_t c_discard[TOTAL_CARDS];
    int discard_count = 0;
    {
        godot_variant v = dict_get(&args_dict, "discard_pile");
        godot_array arr = api->godot_variant_as_array(&v);
        discard_count = encode_card_array(&arr, c_discard, TOTAL_CARDS);
        api->godot_array_destroy(&arr);
        api->godot_variant_destroy(&v);
    }

    /* -- Decode led_suit -- */
    int led_suit = -1;
    {
        godot_variant v = dict_get(&args_dict, "led_suit");
        led_suit = (int)api->godot_variant_as_int(&v);
        api->godot_variant_destroy(&v);
    }

    /* -- Decode void_tracker -- */
    uint8_t c_void[NUM_PLAYERS] = {0};
    {
        godot_variant v = dict_get(&args_dict, "void_tracker");
        godot_array arr = api->godot_variant_as_array(&v);
        encode_void_tracker(&arr, c_void);
        api->godot_array_destroy(&arr);
        api->godot_variant_destroy(&v);
    }

    /* -- Decode discard_starters (optional) -- */
    int c_discard_starters[TOTAL_CARDS] = {0};
    int discard_trick_count = 0;
    if (dict_has(&args_dict, "discard_starters")) {
        godot_variant v = dict_get(&args_dict, "discard_starters");
        godot_array arr = api->godot_variant_as_array(&v);
        discard_trick_count = decode_int_array(&arr, c_discard_starters, TOTAL_CARDS);
        api->godot_array_destroy(&arr);
        api->godot_variant_destroy(&v);
    }

    /* -- Decode player_index (optional, default 0) -- */
    int player_index = 0;
    if (dict_has(&args_dict, "player_index")) {
        godot_variant v = dict_get(&args_dict, "player_index");
        player_index = (int)api->godot_variant_as_int(&v);
        api->godot_variant_destroy(&v);
    }

    /* -- Build SearchParams -- */
    SearchParams params;
    memset(&params, 0, sizeof(params));
    params.iterations = 2000; /* Increased 10x for true MCTS depth */
    params.sims_per_det = 10;
    params.block_leader = 0;
    params.time_limit_ms = 0;
    params.human_index = -1;
    params.current_round = 1;
    params.total_rounds = 5;
    for (int i=0; i<NUM_PLAYERS; i++) params.player_in_game_ids[i] = i+1;

    if (dict_has(&args_dict, "player_in_game_ids")) {
        godot_variant v = dict_get(&args_dict, "player_in_game_ids");
        godot_array arr = api->godot_variant_as_array(&v);
        decode_int_array(&arr, params.player_in_game_ids, NUM_PLAYERS);
        api->godot_array_destroy(&arr);
        api->godot_variant_destroy(&v);
    }

    if (dict_has(&args_dict, "iterations")) {
        godot_variant v = dict_get(&args_dict, "iterations");
        params.iterations = (int)api->godot_variant_as_int(&v);
        api->godot_variant_destroy(&v);
    }
    if (dict_has(&args_dict, "sims_per_det")) {
        godot_variant v = dict_get(&args_dict, "sims_per_det");
        params.sims_per_det = (int)api->godot_variant_as_int(&v);
        api->godot_variant_destroy(&v);
    }
    if (dict_has(&args_dict, "time_limit_ms")) {
        godot_variant v = dict_get(&args_dict, "time_limit_ms");
        params.time_limit_ms = (int)api->godot_variant_as_int(&v);
        api->godot_variant_destroy(&v);
    }
    if (dict_has(&args_dict, "block_leader")) {
        godot_variant v = dict_get(&args_dict, "block_leader");
        params.block_leader = api->godot_variant_as_bool(&v) ? 1 : 0;
        api->godot_variant_destroy(&v);
    }
    if (dict_has(&args_dict, "human_index")) {
        godot_variant v = dict_get(&args_dict, "human_index");
        params.human_index = (int)api->godot_variant_as_int(&v);
        api->godot_variant_destroy(&v);
    }
    if (dict_has(&args_dict, "current_round")) {
        godot_variant v = dict_get(&args_dict, "current_round");
        params.current_round = (int)api->godot_variant_as_int(&v);
        api->godot_variant_destroy(&v);
    }
    if (dict_has(&args_dict, "total_rounds")) {
        godot_variant v = dict_get(&args_dict, "total_rounds");
        params.total_rounds = (int)api->godot_variant_as_int(&v);
        api->godot_variant_destroy(&v);
    }
    if (dict_has(&args_dict, "cumulative_scores")) {
        godot_variant v = dict_get(&args_dict, "cumulative_scores");
        godot_array arr = api->godot_variant_as_array(&v);
        decode_double_array(&arr, params.cumulative_scores, NUM_PLAYERS);
        api->godot_array_destroy(&arr);
        api->godot_variant_destroy(&v);
    }

    /* -- Call the C MCTS engine -- */
    SearchResult result;
    memset(&result, 0, sizeof(result));

    mcts_search_c(
        c_deck, c_hand, hand_size,
        c_bids, c_tricks_won, current_turn,
        c_played, played_count,
        trick_starter, dealer_index,
        c_discard, discard_count,
        led_suit, c_void,
        c_discard_starters, discard_trick_count,
        player_index,
        &params, &result
    );

    /* -- Build return Dictionary -- */
    godot_dictionary ret_dict;
    api->godot_dictionary_new(&ret_dict);

    /* best_card */
    {
        char card_buf[5];
        decode_card_to_str(result.best_card, card_buf);
        godot_string gs = gd_string_from_cstr(card_buf);
        godot_variant var_card;
        api->godot_variant_new_string(&var_card, &gs);
        dict_set(&ret_dict, "best_card", &var_card);
        api->godot_variant_destroy(&var_card);
        api->godot_string_destroy(&gs);
    }

    /* actions: Dictionary of card_string -> {"v": int, "w": float, "avg": float} */
    {
        godot_dictionary actions_dict;
        api->godot_dictionary_new(&actions_dict);

        for (int i = 0; i < result.num_actions; i++) {
            ActionStat *a = &result.actions[i];

            /* Card key */
            char card_buf[5];
            decode_card_to_str(a->card, card_buf);
            godot_string gs_card = gd_string_from_cstr(card_buf);
            godot_variant var_card_key;
            api->godot_variant_new_string(&var_card_key, &gs_card);

            /* Stats sub-dictionary */
            godot_dictionary stat_dict;
            api->godot_dictionary_new(&stat_dict);

            {
                godot_variant v_visits;
                api->godot_variant_new_int(&v_visits, a->visits);
                dict_set(&stat_dict, "v", &v_visits);
                api->godot_variant_destroy(&v_visits);
            }
            {
                godot_variant v_reward;
                api->godot_variant_new_real(&v_reward, a->total_reward);
                dict_set(&stat_dict, "w", &v_reward);
                api->godot_variant_destroy(&v_reward);
            }
            {
                godot_variant v_avg;
                api->godot_variant_new_real(&v_avg, a->avg);
                dict_set(&stat_dict, "avg", &v_avg);
                api->godot_variant_destroy(&v_avg);
            }

            godot_variant var_stat;
            api->godot_variant_new_dictionary(&var_stat, &stat_dict);
            api->godot_dictionary_set(&actions_dict, &var_card_key, &var_stat);

            api->godot_variant_destroy(&var_stat);
            api->godot_dictionary_destroy(&stat_dict);
            api->godot_variant_destroy(&var_card_key);
            api->godot_string_destroy(&gs_card);
        }

        godot_variant var_actions;
        api->godot_variant_new_dictionary(&var_actions, &actions_dict);
        dict_set(&ret_dict, "actions", &var_actions);
        api->godot_variant_destroy(&var_actions);
        api->godot_dictionary_destroy(&actions_dict);
    }

    api->godot_dictionary_destroy(&args_dict);

    /* Return */
    api->godot_variant_destroy(&ret);
    api->godot_variant_new_dictionary(&ret, &ret_dict);
    api->godot_dictionary_destroy(&ret_dict);

    return ret;
}

/* ======================================================================
 * bl_select_card(args_dict: Dictionary) -> Dictionary
 *
 * Input keys (all required unless noted):
 *   "hand"            : Array of String (current hand)
 *   "legal"           : Array of String (current legal cards)
 *   "bids"            : Array of int (4 elements)
 *   "tricks_won"      : Array of int (4 elements)
 *   "player_index"    : int (0..3)
 *   "dealer_index"    : int (optional, default 0)
 *   "throw_turn"      : int (0..3)
 *   "play_turn"       : int (optional, default 0)
 *   "cards_played"    : Array of String (current trick, in order)
 *   "trick_starter"   : int (starter for current trick)
 *   "discard_tricks"  : Array of Array of String (optional, past tricks in order)
 *   "discard_starters": Array of int (optional, starters per past trick)
 *
 * Returns:
 *   {"card": String}
 * ====================================================================== */
static godot_variant bl_select_card_method(godot_object *p_instance, void *p_method_data,
                                           void *p_user_data, int p_num_args,
                                           godot_variant **p_args) {
    (void)p_instance;
    (void)p_method_data;
    (void)p_user_data;

    godot_variant ret;
    api->godot_variant_new_nil(&ret);

    if (p_num_args < 1) {
        return ret;
    }

    godot_dictionary args_dict = api->godot_variant_as_dictionary(p_args[0]);

    /* Decode hand */
    uint8_t c_hand[CARDS_PER_HAND];
    int hand_size = 0;
    {
        godot_variant v = dict_get(&args_dict, "hand");
        godot_array arr = api->godot_variant_as_array(&v);
        hand_size = encode_card_array(&arr, c_hand, CARDS_PER_HAND);
        api->godot_array_destroy(&arr);
        api->godot_variant_destroy(&v);
    }

    /* Decode legal */
    uint8_t c_legal[MAX_LEGAL];
    int legal_count = 0;
    {
        godot_variant v = dict_get(&args_dict, "legal");
        godot_array arr = api->godot_variant_as_array(&v);
        legal_count = encode_card_array(&arr, c_legal, MAX_LEGAL);
        api->godot_array_destroy(&arr);
        api->godot_variant_destroy(&v);
    }

    if (legal_count <= 0) {
        api->godot_dictionary_destroy(&args_dict);
        return ret;
    }

    /* Decode bids */
    int c_bids[NUM_PLAYERS] = {0};
    {
        godot_variant v = dict_get(&args_dict, "bids");
        godot_array arr = api->godot_variant_as_array(&v);
        decode_int_array(&arr, c_bids, NUM_PLAYERS);
        api->godot_array_destroy(&arr);
        api->godot_variant_destroy(&v);
    }

    /* Decode tricks_won */
    int c_tricks_won[NUM_PLAYERS] = {0};
    {
        godot_variant v = dict_get(&args_dict, "tricks_won");
        godot_array arr = api->godot_variant_as_array(&v);
        decode_int_array(&arr, c_tricks_won, NUM_PLAYERS);
        api->godot_array_destroy(&arr);
        api->godot_variant_destroy(&v);
    }

    /* Decode player_index */
    int player_index = 0;
    {
        godot_variant v = dict_get(&args_dict, "player_index");
        player_index = (int)api->godot_variant_as_int(&v);
        api->godot_variant_destroy(&v);
    }

    /* Decode dealer_index (optional) */
    int dealer_index = 0;
    if (dict_has(&args_dict, "dealer_index")) {
        godot_variant v = dict_get(&args_dict, "dealer_index");
        dealer_index = (int)api->godot_variant_as_int(&v);
        api->godot_variant_destroy(&v);
    }

    /* Decode throw_turn */
    int throw_turn = 0;
    {
        godot_variant v = dict_get(&args_dict, "throw_turn");
        throw_turn = (int)api->godot_variant_as_int(&v);
        api->godot_variant_destroy(&v);
    }

    /* Decode play_turn (optional) */
    int play_turn = 0;
    if (dict_has(&args_dict, "play_turn")) {
        godot_variant v = dict_get(&args_dict, "play_turn");
        play_turn = (int)api->godot_variant_as_int(&v);
        api->godot_variant_destroy(&v);
    }

    /* Decode cards_played (current trick) */
    uint8_t c_played[NUM_PLAYERS];
    int played_count = 0;
    {
        godot_variant v = dict_get(&args_dict, "cards_played");
        godot_array arr = api->godot_variant_as_array(&v);
        played_count = encode_card_array(&arr, c_played, NUM_PLAYERS);
        api->godot_array_destroy(&arr);
        api->godot_variant_destroy(&v);
    }

    /* Decode trick_starter */
    int trick_starter = 0;
    {
        godot_variant v = dict_get(&args_dict, "trick_starter");
        trick_starter = (int)api->godot_variant_as_int(&v);
        api->godot_variant_destroy(&v);
    }

    /* Initialize bot logic */
    BotLogicC bl;
    bl_init(&bl, c_hand, hand_size,
            c_bids[player_index], c_tricks_won[player_index], player_index);

    /* Replay past tricks if provided */
    if (dict_has(&args_dict, "discard_tricks")) {
        godot_variant v_tricks = dict_get(&args_dict, "discard_tricks");
        godot_array tricks_arr = api->godot_variant_as_array(&v_tricks);

        int starters_count = 0;
        int starters[NUM_SUITS * CARDS_PER_HAND] = {0};
        if (dict_has(&args_dict, "discard_starters")) {
            godot_variant v_starters = dict_get(&args_dict, "discard_starters");
            godot_array starters_arr = api->godot_variant_as_array(&v_starters);
            starters_count = decode_int_array(&starters_arr, starters,
                                              NUM_SUITS * CARDS_PER_HAND);
            api->godot_array_destroy(&starters_arr);
            api->godot_variant_destroy(&v_starters);
        }

        int trick_total = api->godot_array_size(&tricks_arr);
        int hist_trick_starter = (dealer_index + 1) % NUM_PLAYERS;
        for (int t = 0; t < trick_total; t++) {
            bl_on_throw_turn_started(&bl, t);
            godot_variant v_trick = api->godot_array_get(&tricks_arr, t);
            godot_array trick_arr = api->godot_variant_as_array(&v_trick);
            uint8_t trick_cards[NUM_PLAYERS];
            int trick_count = encode_card_array(&trick_arr, trick_cards, NUM_PLAYERS);

            int starter = (t < starters_count) ? starters[t] : hist_trick_starter;
            for (int j = 0; j < trick_count; j++) {
                int player = (starter + j) % NUM_PLAYERS;
                bl_on_card_selected(&bl, trick_cards[j], j, player == player_index);
            }
            if (trick_count == NUM_PLAYERS) {
                bl_on_trick_completed(&bl, trick_cards, trick_count, starter);
                int wi = get_winning_index_local(trick_cards, trick_count);
                hist_trick_starter = (starter + wi) % NUM_PLAYERS;
            }

            api->godot_array_destroy(&trick_arr);
            api->godot_variant_destroy(&v_trick);
        }

        api->godot_array_destroy(&tricks_arr);
        api->godot_variant_destroy(&v_tricks);

        if (play_turn >= trick_total) {
            bl_on_throw_turn_started(&bl, play_turn);
        }
    } else {
        bl_on_throw_turn_started(&bl, play_turn);
    }

    /* Replay current trick cards */
    for (int tt = 0; tt < played_count; tt++) {
        int player = (trick_starter + tt) % NUM_PLAYERS;
        bl_on_card_selected(&bl, c_played[tt], tt, player == player_index);
    }

    /* Set legal cards */
    memcpy(bl.legal, c_legal, (size_t)legal_count);
    bl.legal_count = legal_count;

    /* Select */
    uint8_t card = bl_select_card(&bl, throw_turn, c_played, played_count,
                                  dealer_index, c_bids);

    /* Build return Dictionary */
    godot_dictionary ret_dict;
    api->godot_dictionary_new(&ret_dict);
    {
        char card_buf[5];
        decode_card_to_str(card, card_buf);
        godot_string gs = gd_string_from_cstr(card_buf);
        godot_variant var_card;
        api->godot_variant_new_string(&var_card, &gs);
        dict_set(&ret_dict, "card", &var_card);
        api->godot_variant_destroy(&var_card);
        api->godot_string_destroy(&gs);
    }

    api->godot_dictionary_destroy(&args_dict);
    api->godot_variant_destroy(&ret);
    api->godot_variant_new_dictionary(&ret, &ret_dict);
    api->godot_dictionary_destroy(&ret_dict);

    return ret;
}

/* ======================================================================
 * GDNative lifecycle
 * ====================================================================== */

GDN_EXPORT void godot_gdnative_init(godot_gdnative_init_options *p_options) {
    api = p_options->api_struct;

    /* Find nativescript extension */
    for (unsigned int i = 0; i < api->num_extensions; i++) {
        if (api->extensions[i]->type == GDNATIVE_EXT_NATIVESCRIPT) {
            nativescript_api = (const godot_gdnative_ext_nativescript_api_struct *)api->extensions[i];
            break;
        }
    }
}

GDN_EXPORT void godot_gdnative_terminate(godot_gdnative_terminate_options *p_options) {
    (void)p_options;
    api = NULL;
    nativescript_api = NULL;
}

GDN_EXPORT void godot_nativescript_init(void *p_handle) {
    /* Register MCTSEngine class */
    godot_instance_create_func create_func = {
        .create_func = &mcts_engine_constructor,
        .method_data = NULL,
        .free_func = NULL,
    };
    godot_instance_destroy_func destroy_func = {
        .destroy_func = &mcts_engine_destructor,
        .method_data = NULL,
        .free_func = NULL,
    };
    nativescript_api->godot_nativescript_register_class(
        p_handle, "MCTSEngine", "Reference",
        create_func, destroy_func
    );

    /* Register BotLogicEngine class */
    godot_instance_create_func create_bl = {
        .create_func = &bot_logic_engine_constructor,
        .method_data = NULL,
        .free_func = NULL,
    };
    godot_instance_destroy_func destroy_bl = {
        .destroy_func = &bot_logic_engine_destructor,
        .method_data = NULL,
        .free_func = NULL,
    };
    nativescript_api->godot_nativescript_register_class(
        p_handle, "BotLogicEngine", "Reference",
        create_bl, destroy_bl
    );

    /* Register mcts_search method */
    godot_method_attributes method_attr = {
        .rpc_type = GODOT_METHOD_RPC_MODE_DISABLED,
    };
    godot_instance_method method = {
        .method = &mcts_search_method,
        .method_data = NULL,
        .free_func = NULL,
    };
    nativescript_api->godot_nativescript_register_method(
        p_handle, "MCTSEngine", "mcts_search",
        method_attr, method
    );

    /* Register bl_select_card method */
    godot_instance_method bl_method = {
        .method = &bl_select_card_method,
        .method_data = NULL,
        .free_func = NULL,
    };
    nativescript_api->godot_nativescript_register_method(
        p_handle, "BotLogicEngine", "bl_select_card",
        method_attr, bl_method
    );
}
