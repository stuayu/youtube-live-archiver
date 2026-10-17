"""HTTPS transport that follows the configured address-family preference."""
import http.client
import socket
from urllib.request import HTTPSHandler, build_opener

from .youtube_ip import current_mode


class _FamilyHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        mode = current_mode()
        families = {
            "ipv6": (socket.AF_INET6,),
            "ipv4": (socket.AF_INET,),
            "prefer_ipv6": (socket.AF_INET6, socket.AF_INET),
            "auto": (socket.AF_UNSPEC,),
        }[mode]
        errors = []
        for family in families:
            try:
                addresses = socket.getaddrinfo(self.host, self.port, family,
                                               socket.SOCK_STREAM)
            except OSError as error:
                errors.append(error)
                continue
            for af, socktype, proto, _, address in addresses:
                sock = socket.socket(af, socktype, proto)
                sock.settimeout(self.timeout)
                try:
                    sock.connect(address)
                    self.sock = sock
                    if self._tunnel_host:
                        self._tunnel()
                    self.sock = self._context.wrap_socket(
                        self.sock, server_hostname=self._tunnel_host or self.host
                    )
                    return
                except OSError as error:
                    errors.append(error)
                    if family == socket.AF_INET6:
                        from .youtube_ip import note_ipv6_failure
                        note_ipv6_failure(str(error))
                    if self.sock is sock:
                        sock.close()
                    elif self.sock:
                        self.sock.close()
        if errors:
            raise errors[-1]
        raise OSError("hostname resolved to no addresses")


class _FamilyHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        return self.do_open(_FamilyHTTPSConnection, request, context=self._context,
                            check_hostname=self._check_hostname)


def open_url(request, *, timeout: float):
    """Open HTTPS with IPv6-first fallback according to YOUTUBE_IP_MODE."""
    return build_opener(_FamilyHTTPSHandler()).open(request, timeout=timeout)
