# -*- coding: utf-8 -*-
# gui/auth.py
"""
Handles:
- Password hashing and verification
- IP whitelist management
- Persistent config storage
"""

import json
import hashlib
import os
from pathlib import Path
from typing import List, Optional


class AuthManager:
    """
    Manages authentication and security for the web GUI.
    
    Stores config in config.json with password hash and IP whitelist.
    """
    
    def __init__(self, config_file: str = 'gui/config.json'):
        self.config_file = Path(config_file)
        self.config = self._load_config()
    
    def _load_config(self) -> dict:
        """Load config from file or create default."""
        if self.config_file.exists():
            with open(self.config_file, 'r') as f:
                return json.load(f)
        
        # Default config
        return {
            'initialized': False,
            'password_hash': None,
            'whitelist_enabled': False,
            'whitelist_ips': []
        }
    
    def _save_config(self):
        """Save config to file."""
        # Create directory if needed
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f, indent=2)
    
    def _hash_password(self, password: str) -> str:
        """Hash password using SHA-256 with salt."""
        salt = "schema2_threading_dashboard"  # Static salt for simplicity
        return hashlib.sha256((password + salt).encode()).hexdigest()
    
    def is_initialized(self) -> bool:
        """Check if auth has been initialized."""
        return self.config.get('initialized', False)
    
    def initialize(self, password: str, whitelist_ips: List[str] = None):
        """
        Initialize authentication with password and optional whitelist.
        
        Args:
            password: Admin password
            whitelist_ips: Optional list of allowed IPs
        """
        self.config['initialized'] = True
        self.config['password_hash'] = self._hash_password(password)
        
        if whitelist_ips:
            self.config['whitelist_enabled'] = True
            self.config['whitelist_ips'] = whitelist_ips
        else:
            self.config['whitelist_enabled'] = False
            self.config['whitelist_ips'] = []
        
        self._save_config()
        
        print("[AUTH] Initialized successfully")
        if self.config['whitelist_enabled']:
            print(f"[AUTH] IP whitelist enabled: {whitelist_ips}")
    
    def verify_password(self, password: str) -> bool:
        """Verify password against stored hash."""
        if not self.is_initialized():
            return False
        
        password_hash = self._hash_password(password)
        return password_hash == self.config['password_hash']
    
    def check_ip_allowed(self, ip: str) -> bool:
        """
        Check if IP is allowed.
        
        Args:
            ip: Client IP address
        
        Returns:
            True if allowed, False if whitelist is enabled and IP not in list
        """
        if not self.config.get('whitelist_enabled', False):
            return True  # Whitelist disabled, all IPs allowed
        
        # Check if IP is in whitelist
        return ip in self.config['whitelist_ips']
    
    def add_ip_to_whitelist(self, ip: str):
        """Add IP to whitelist."""
        if ip not in self.config['whitelist_ips']:
            self.config['whitelist_ips'].append(ip)
            self._save_config()
            print(f"[AUTH] Added IP to whitelist: {ip}")
    
    def remove_ip_from_whitelist(self, ip: str):
        """Remove IP from whitelist."""
        if ip in self.config['whitelist_ips']:
            self.config['whitelist_ips'].remove(ip)
            self._save_config()
            print(f"[AUTH] Removed IP from whitelist: {ip}")
    
    def get_whitelist(self) -> List[str]:
        """Get current IP whitelist."""
        return self.config.get('whitelist_ips', [])
    
    def change_password(self, old_password: str, new_password: str) -> bool:
        """
        Change admin password.
        
        Args:
            old_password: Current password for verification
            new_password: New password to set
        
        Returns:
            True if successful, False if old password incorrect
        """
        if not self.verify_password(old_password):
            return False
        
        self.config['password_hash'] = self._hash_password(new_password)
        self._save_config()
        
        print("[AUTH] Password changed successfully")
        return True
