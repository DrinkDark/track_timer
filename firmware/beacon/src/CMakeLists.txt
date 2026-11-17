idf_component_register(
    SRCS main.c settings.c beacon.c ref_led.c status_led.c links.c console.c
    INCLUDE_DIRS "." "../../common"
    REQUIRES nvs_flash esp_wifi esp_event esp_netif esp_timer driver esp_driver_gptimer esp_driver_uart esp_driver_usb_serial_jtag vfs
)
