#pragma once
#include <stdbool.h>
#include <stdint.h>

void trigger_init(void);
/* Software trigger from the console: the same sequence as a pistol pulse (sound + start packets). */
void trigger_fire_software(void);
/* True when a trigger would be accepted now (lockout over). */
bool trigger_armed(void);
bool trigger_in_lockout(void);
bool trigger_arm_switch_on(void);
uint32_t trigger_count(void);
uint32_t trigger_ignored(void);
void trigger_set_edge(bool rising);
