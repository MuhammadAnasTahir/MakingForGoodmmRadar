"""The web server must accept a burst of simultaneous connections (a browser loading the page)."""
import socket
import sys
import threading
import unittest

from test_gateway import gateway


def burst(server_class, n=40):
    """Open n connections at the same instant, without reading; return how many were refused."""
    server = server_class(('127.0.0.1', 0), gateway.Handler)
    port = server.server_port
    # Do not start serve_forever yet: the connections must wait in the listen queue, as they do
    # while the server is busy with the first requests of a page load.
    refused, socks, lock = [], [], threading.Lock()
    start = threading.Barrier(n)

    def connect():
        s = socket.socket()
        s.settimeout(3)
        start.wait()
        try:
            s.connect(('127.0.0.1', port))
            with lock:
                socks.append(s)
        except OSError:
            with lock:
                refused.append(1)
            s.close()
    threads = [threading.Thread(target=connect) for _ in range(n)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    for s in socks:
        s.close()
    server.server_close()
    return len(refused)


class ServerTests(unittest.TestCase):
    def test_burst_of_connections_is_accepted(self):
        self.assertEqual(burst(gateway.Server), 0)

    @unittest.skipUnless(sys.platform == 'win32', 'port sharing only happens on Windows')
    def test_second_copy_cannot_share_the_port(self):
        first = gateway.Server(('127.0.0.1', 0), gateway.Handler)
        try:
            with self.assertRaises(OSError):
                gateway.Server(('127.0.0.1', first.server_port), gateway.Handler)
        finally:
            first.server_close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
