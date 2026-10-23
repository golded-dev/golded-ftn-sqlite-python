# FTN SQLite message base

This context names the durable things in the SQLite message-base format.

## Language

**Database**:
One SQLite file containing one or more areas.
_Avoid_: Base file, container

**Area**:
A numbered sequence of either netmail or echomail messages. The normal deployment has one area per database, while the format permits several.
_Avoid_: Board, folder

**Netmail**:
A point-to-point FTN message with an origin and an explicit destination address.
_Avoid_: Direct message

**Echomail**:
An FTN message distributed through a named area with ordered routing metadata.
_Avoid_: Conference message

**Message number**:
A positive, monotonic identity local to one area. Deleted numbers remain consumed.
_Avoid_: Database row ID, MSGID

**External message ID**:
The network-visible FTN `MSGID`, when present. It is data, not the database identity.
_Avoid_: Message number, row ID

**Revision**:
A token covering one stored message and its ordered metadata, used to reject stale updates and deletes.
_Avoid_: Version number
