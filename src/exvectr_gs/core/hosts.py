"""Control host check (design 2.5 `server.control_hosts`)."""

import ipaddress


def is_control_host(addr: str, control_hosts: list[str]) -> bool:
    """True if the peer address is one of `control_hosts` (IPs, CIDR networks or `localhost`)."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped  # type: ignore[assignment]
    for entry in control_hosts:
        if entry == "localhost":
            if ip.is_loopback:
                return True
            continue
        try:
            if ip in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False
