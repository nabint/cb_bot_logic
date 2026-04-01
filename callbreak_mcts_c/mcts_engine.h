/*
 * mcts_engine.h — Public API for Callbreak MCTS C engine.
 *
 * Card encoding: uint8_t = (rank << 2) | suit
 *   suit: 0=DIAMONDS, 1=CLUBS, 2=HEARTS, 3=SPADES
 *   Rank extracted: card >> 2
 *   Suit extracted: card & 3
 */

#ifndef MCTS_ENGINE_H
#define MCTS_ENGINE_H

#include <stdint.h>

#define NUM_PLAYERS    4
#define CARDS_PER_HAND 13
#define TOTAL_CARDS    52
#define MAX_LEGAL      13
#define NUM_SUITS      4

/* Suit constants */
#define SUIT_DIAMONDS  0
#define SUIT_CLUBS     1
#define SUIT_HEARTS    2
#define SUIT_SPADES    3

/* Card helpers */
#define CARD_RANK(c)   ((c) >> 2)
#define CARD_SUIT(c)   ((c) & 3)
#define MAKE_CARD(r,s) (((r) << 2) | (s))
#define CARD_NONE      0xFF
#define MAX_TRICKS     13

/* Void tracker: bitmask per player (bit 0=D, 1=C, 2=H, 3=S) */
#define VOID_BIT(suit) (1 << (suit))

/* Per-completed-trick record */
typedef struct {
    uint8_t cards[NUM_PLAYERS];   /* cards[i] = card played by (starter+i)%4 */
    int     starter;              /* player index who led this trick */
    int     winner;               /* player index who won */
    int     led_suit;             /* suit of cards[0] */
    int     player_ids[NUM_PLAYERS]; /* stable in-game IDs for each played card */
} TrickRecord;

/* Inference context built from observed play history */
typedef struct {
    TrickRecord tricks[MAX_TRICKS];
    int         trick_count;

    /* Current partial trick */
    uint8_t     current_trick[NUM_PLAYERS];
    int         current_trick_count;
    int         current_trick_starter;

    /* Per-player tracking derived from trick replay */
    uint8_t     played_cards_by_player[NUM_PLAYERS][CARDS_PER_HAND];
    int         played_card_counts[NUM_PLAYERS];

    uint8_t     void_suits[NUM_PLAYERS];       /* bitmask per player */
    int         remaining_card_counts[NUM_PLAYERS];

    /* Suit-follow counts: how many times player followed suit when they could */
    int         suit_follow_count[NUM_PLAYERS][NUM_SUITS];
    /* Trump counts: how many times player trumped a non-spade trick */
    int         trump_count[NUM_PLAYERS];
    /* Low-discard flag: player discarded low off-suit */
    int         low_discard_suits[NUM_PLAYERS]; /* bitmask of suits where player discarded low */

    /* Specific honor tracking */
    uint16_t    played_honors[NUM_SUITS];       /* bitmask of specific cards played per suit (1 << rank) */
    int         suit_led_count[NUM_SUITS];      /* how many times each suit was led */

    /* Deductive bounds from "must play higher" rule */
    int         max_rank[NUM_PLAYERS][NUM_SUITS]; 

    int         player_index;                  /* root MCTS player */
    int         dealer_index;
    int         player_in_game_ids[NUM_PLAYERS];
} InferenceCtx;

/* Game state for one round */
typedef struct {
    uint8_t hands[NUM_PLAYERS][CARDS_PER_HAND];
    int     hand_sizes[NUM_PLAYERS];
    int     bids[NUM_PLAYERS];
    int     tricks_won[NUM_PLAYERS];
    int     current_turn;
    uint8_t cards_played[NUM_PLAYERS];   /* current trick */
    int     cards_played_count;
    int     trick_starter;
    int     dealer_index;
    int     led_suit;                    /* -1 if none */
    int     total_cards_played;
    
    /* Authoritative history tracking */
    uint8_t   played_cards_by_player[NUM_PLAYERS][CARDS_PER_HAND];
    int       played_card_counts[NUM_PLAYERS];
    TrickRecord trick_history[MAX_TRICKS];
    int       completed_tricks_count;
    uint8_t   void_suits_by_player[NUM_PLAYERS]; /* bitmask */
    int       remaining_card_counts[NUM_PLAYERS];
    int       in_game_ids[NUM_PLAYERS];   /* Stable player IDs */
} CallbreakState;

/* Action statistics */
typedef struct {
    uint8_t card;
    int     visits;
    double  total_reward;
    double  avg;
} ActionStat;

/* Search parameters */
typedef struct {
    int    iterations;
    int    sims_per_det;
    int    block_leader;
    double cumulative_scores[NUM_PLAYERS];
    int    time_limit_ms;   /* 0 = use iterations */
    int    human_index;     /* -1 if unknown */
    int    current_round;   /* 1-based current match round */
    int    total_rounds;    /* total match rounds, fixed to 5 for standard */
    int    player_in_game_ids[NUM_PLAYERS];
} SearchParams;

/* Search result */
typedef struct {
    uint8_t    best_card;
    int        num_actions;
    ActionStat actions[MAX_LEGAL];
} SearchResult;

/* ---- Public API ---- */

/* Compute legal cards for a player given the current trick state.
 * Enforces all Callbreak rules: suit-following, must-play-higher,
 * trump-forcing, and higher-trump-forcing.
 * Returns the count of legal cards written to out_legal. */
int get_legal_cards_c(
    const uint8_t *hand, int hand_size,
    const uint8_t *played, int played_count,
    int led_suit,
    uint8_t *out_legal
);

/* Advance the authoritative rollout state by one played card. */
void state_play_card_inplace_c(CallbreakState *state, uint8_t card);

void mcts_search_c(
    const uint8_t *original_deck,
    const uint8_t *known_hand,
    int known_hand_size,
    const int *bids,
    const int *tricks_won,
    int current_turn,
    const uint8_t *cards_played,
    int cards_played_count,
    int trick_starter,
    int dealer_index,
    const uint8_t *discard_pile,
    int discard_count,
    int led_suit,
    const uint8_t *void_tracker,   /* 4 bytes, bitmask per player */
    const int *discard_starters,   /* per-trick starter player indices */
    int discard_trick_count,       /* number of completed tricks */
    int player_index,
    const SearchParams *params,
    SearchResult *result
);

void determinize_hidden_hands_c(
    const uint8_t *original_deck,
    const uint8_t *known_hand,
    int known_hand_size,
    const int *bids,
    const int *tricks_won,
    int current_turn,
    const uint8_t *cards_played,
    int cards_played_count,
    int trick_starter,
    int dealer_index,
    const uint8_t *discard_pile,
    int discard_count,
    int led_suit,
    const uint8_t *void_tracker,
    const int *discard_starters,
    int discard_trick_count,
    int player_index,
    const int *player_in_game_ids,
    uint8_t *out_hands,
    int *out_hand_sizes
);

void debug_inference_snapshot_c(
    const uint8_t *known_hand,
    int known_hand_size,
    const int *tricks_won,
    const uint8_t *cards_played,
    int cards_played_count,
    int trick_starter,
    int dealer_index,
    const uint8_t *discard_pile,
    int discard_count,
    const uint8_t *void_tracker,
    const int *discard_starters,
    int discard_trick_count,
    int player_index,
    const int *player_in_game_ids,
    uint8_t *out_voids,
    int *out_max_ranks
);

#endif /* MCTS_ENGINE_H */
