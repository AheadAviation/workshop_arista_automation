#!/usr/bin/env python3
"""
Ansible Filter Plugins for Arista EOS Configuration Generation

These filters help convert NetBox interface naming conventions
to Arista EOS standard interface names.
"""

import re


def arista_interface_name(interface_name):
    """
    Convert interface name to Arista EOS format.
    
    Examples:
        eth1 -> Ethernet1
        eth1/1 -> Ethernet1/1
        Ethernet1 -> Ethernet1 (unchanged)
        Management0 -> Management0 (unchanged)
        Loopback0 -> Loopback0 (unchanged)
        port-channel1 -> Port-Channel1
        vlan100 -> Vlan100
    """
    if not interface_name:
        return interface_name
    
    name_lower = interface_name.lower()
    
    # Handle eth -> Ethernet conversion
    if name_lower.startswith('eth') and not name_lower.startswith('ethernet'):
        # Extract the number/identifier after 'eth'
        suffix = interface_name[3:]
        return f"Ethernet{suffix}"
    
    # Handle ethernet -> Ethernet (capitalize properly)
    if name_lower.startswith('ethernet'):
        suffix = interface_name[8:]
        return f"Ethernet{suffix}"
    
    # Handle port-channel -> Port-Channel
    if name_lower.startswith('port-channel'):
        suffix = interface_name[12:]
        return f"Port-Channel{suffix}"
    
    # Handle vlan -> Vlan
    if name_lower.startswith('vlan'):
        suffix = interface_name[4:]
        return f"Vlan{suffix}"
    
    # Handle loopback -> Loopback
    if name_lower.startswith('loopback'):
        suffix = interface_name[8:]
        return f"Loopback{suffix}"
    
    # Handle management -> Management
    if name_lower.startswith('management'):
        suffix = interface_name[10:]
        return f"Management{suffix}"
    
    # Return unchanged if no conversion needed
    return interface_name


def interface_short_name(interface_name):
    """
    Convert full interface name to short name.
    
    Examples:
        Ethernet1 -> Et1
        Ethernet1/1 -> Et1/1
        Port-Channel1 -> Po1
        Vlan100 -> Vl100
        Loopback0 -> Lo0
        Management0 -> Ma0
    """
    if not interface_name:
        return interface_name
    
    name_lower = interface_name.lower()
    
    if name_lower.startswith('ethernet'):
        suffix = interface_name[8:]
        return f"Et{suffix}"
    
    if name_lower.startswith('port-channel'):
        suffix = interface_name[12:]
        return f"Po{suffix}"
    
    if name_lower.startswith('vlan'):
        suffix = interface_name[4:]
        return f"Vl{suffix}"
    
    if name_lower.startswith('loopback'):
        suffix = interface_name[8:]
        return f"Lo{suffix}"
    
    if name_lower.startswith('management'):
        suffix = interface_name[10:]
        return f"Ma{suffix}"
    
    return interface_name


def peer_ip_from_local(local_ip, network_mask=31):
    """
    Calculate peer IP from local IP assuming point-to-point /31 network.
    
    For /31 networks:
    - If local IP ends in even number -> peer is +1
    - If local IP ends in odd number -> peer is -1
    """
    import ipaddress
    
    if not local_ip:
        return None
    
    # Handle CIDR notation
    if '/' in local_ip:
        ip = local_ip.split('/')[0]
    else:
        ip = local_ip
    
    try:
        addr = ipaddress.ip_address(ip)
        last_octet = int(addr) & 0xFF
        
        if last_octet % 2 == 0:
            peer = addr + 1
        else:
            peer = addr - 1
        
        return str(peer)
    except ValueError:
        return None


class FilterModule:
    """Ansible filter module for Arista EOS configuration."""
    
    def filters(self):
        return {
            'arista_interface_name': arista_interface_name,
            'interface_short_name': interface_short_name,
            'peer_ip_from_local': peer_ip_from_local,
        }

