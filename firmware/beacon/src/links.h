#pragma once
/* Starts both link receivers (RS485 on the cable, ESP-NOW on the radio). */
void links_init(void);
void links_set_channel(int channel);
