# Chat Bridging Relay

Relays messages between different chat services.

Currently supported connection methods:
* IRC client
* Unreal IRCd server
* Slack bot

In progress:
* Matrix

You can configure an arbitrary number of connections, and route messages
between any/all of them. Take a look at `config-example.yaml` for more details.

The model is flexible enough to support any service with a concept of users,
channels/rooms, and messages. Contributions welcome.

## Adding a protocol

Protocol names are mapped to a specific module and object in the `protocols`
dictionary in the config file. You can add protocols from any importable
module.

These objects must define an `init_connection()` method, which accepts a parsed
entry from the `servers` list in the config file. Calling this method must add
an entry to the `chatrelay.servers` dictionary (in memory), mapping the `name`
config property to another object.

These objects must define a `relay_message()` method. This must accept 2 or 4
arguments: `destchan, message, [fromnick], [fromserver]`. If the last 2
arguments are omitted, the message should be treated as a service message, such
as notice of a connection failure. Otherwise, its a normal user message. The
`fromserver` argument is the `name` of the connection from which the message
originated, and can be used to disambiguate the nickname if needed.

Upon receipt of a message, you should interpret your configured `channel_map`
dictionary, calling `relay_message()` on the appropriate objects in
`chatrelay.servers`.
