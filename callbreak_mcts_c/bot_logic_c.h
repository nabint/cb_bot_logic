/*
 * bot_logic_c.h — C port of bot_logic.py for MCTS rollouts.
 * Provides rule-based card selection for Callbreak.
 */

#ifndef BOT_LOGIC_C_H
#define BOT_LOGIC_C_H

#include "mcts_engine.h"

#define MAX_STRATEGIES 20
#define MAX_RANKS      13

/* Strategy types (from bid_logic.py) */
#define STRAT_BRING_DOWN_ACE   0
#define STRAT_BRING_DOWN_KING  1
#define STRAT_PREPARE_FOR_CUT  2
#define STRAT_COMPETE_SPADES   3

typedef struct {
    int type;
    int suit;
} PlayStrategy;

typedef struct {
    int player_idx;
    int bid;
    int tricks_won;

    /* Cards by suit: ranks sorted descending */
    int suit_ranks[NUM_SUITS][MAX_RANKS];
    int suit_counts[NUM_SUITS];

    /* Unrevealed ranks per suit: sorted descending */
    int unrevealed[NUM_SUITS][MAX_RANKS];
    int unrevealed_counts[NUM_SUITS];

    /* Current legal cards */
    uint8_t legal[MAX_LEGAL];
    int legal_count;

    /* Full hand as encoded cards */
    uint8_t hand[CARDS_PER_HAND];
    int hand_size;

    /* Play strategies */
    PlayStrategy strategies[MAX_STRATEGIES];
    int strategy_count;

    /* Tracking */
    int suit_play_turns[NUM_SUITS];
    int cut_trick_played[NUM_SUITS]; /* D,C,H only */

    double total_from_spades;
    double spare_spades;
} BotLogicC;

/* Initialize bot from hand */
void bl_init(BotLogicC *bl, const uint8_t *hand, int hand_size,
             int bid, int tricks_won, int player_idx);

/* Select a card to play */
uint8_t bl_select_card(BotLogicC *bl, int throw_turn,
                       const uint8_t *played, int played_count,
                       int dealer_idx, const int *all_bids);

/* Event: card was played by anyone */
void bl_on_card_selected(BotLogicC *bl, uint8_t card,
                         int throw_turn, int is_self);

/* Event: trick completed */
void bl_on_trick_completed(BotLogicC *bl, const uint8_t *played,
                           int played_count, int starter_idx);

/* Event: throw turn started */
void bl_on_throw_turn_started(BotLogicC *bl, int play_turn);

void compute_state_utilities_c(const CallbreakState *state, int root_player,
                               int block_leader, const double *cumulative_scores,
                               int human_index, int current_round,
                               int total_rounds, double *out_utilities);

void bot_logic_rollout_vector(CallbreakState *state, int root_player,
                              int block_leader, const double *cumulative_scores,
                              int human_index, int current_round,
                              int total_rounds, double *out_utilities);

double bot_logic_rollout(CallbreakState *state, int root_player,
                         int block_leader, const double *cumulative_scores,
                         int human_index, int current_round,
                         int total_rounds);

#endif /* BOT_LOGIC_C_H */
