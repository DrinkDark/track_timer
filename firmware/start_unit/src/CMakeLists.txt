idf_component_register(
    SRCS main.c settings.c sound.c audio.c links.c trigger.c camtrig.c console.c
    INCLUDE_DIRS "." "../../common"
    REQUIRES nvs_flash esp_wifi esp_event esp_netif esp_timer driver esp_driver_i2s esp_driver_gptimer esp_driver_uart
             esp_driver_usb_serial_jtag esp_driver_gpio spi_flash vfs esp_partition
)
