import sys

with open("mcts_engine.c", "r") as f:
    lines = f.readlines()

new_lines = []
skip = False
for i, line in enumerate(lines):
    if i == 1075: # 0-indexed line 1076
        new_lines.append('    /* PIMC Phase: Evaluate EVERY legal action EXACTLY ONCE for this determinized world */\n')
        new_lines.append('    for (int action_idx = 0; action_idx < legal_count; action_idx++) {\n')
        new_lines.append('        CallbreakState rollout_state;\n')
        new_lines.append('        _state_copy(&rollout_state, &base);\n')
        new_lines.append('        _state_play_card_inplace(&rollout_state, legal[action_idx]);\n')
        new_lines.append('        \n')
        new_lines.append('        /* Evaluate the immediate state before rollout */\n')
        new_lines.append('        double state_eval = _evaluate_state_heuristic(&rollout_state, player_index);\n')
        new_lines.append('\n')
        new_lines.append('        double rollout_reward = _rollout(&rollout_state, player_index,\n')
        new_lines.append('                                 params->block_leader, params->cumulative_scores,\n')
        new_lines.append('                                 params->human_index, discard_pile, discard_count);\n')
        new_lines.append('\n')
        new_lines.append('        /* Mix rollout reward with leaf node static evaluation */\n')
        new_lines.append('        #define MCTS_HEURISTIC_WEIGHT 0.35\n')
        new_lines.append('        double reward = (1.0 - MCTS_HEURISTIC_WEIGHT) * rollout_reward + MCTS_HEURISTIC_WEIGHT * state_eval;\n')
        new_lines.append('\n')
        new_lines.append('        /* Aggregate directly into global stats */\n')
        new_lines.append('        action_visits[action_idx]++;\n')
        new_lines.append('        action_rewards[action_idx] += reward;\n')
        new_lines.append('    }\n')
        skip = True
    elif i == 1150: # The closing brace of the aggregate loop
        skip = False
        continue
    
    if not skip:
        new_lines.append(line)

with open("mcts_engine.c", "w") as f:
    f.writelines(new_lines)
