import socket
import threading

import webview

from app import app


def find_free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def main():
    port = find_free_port()

    server_thread = threading.Thread(
        target=app.run,
        kwargs={"host": "127.0.0.1", "port": port, "debug": False, "use_reloader": False},
        daemon=True,
    )
    server_thread.start()

    webview.create_window(
        "EFT Flea",
        url=f"http://127.0.0.1:{port}",
        width=980,
        height=680,
        min_size=(700, 450),
    )
    webview.start()


if __name__ == "__main__":
    main()
