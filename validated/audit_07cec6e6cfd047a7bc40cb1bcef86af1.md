### Title
Log spoofing via unsanitized `Any.type_url` field embedded in `TextFormat::Printer::PrintAny()` warning logs - (File: `src/google/protobuf/text_format.cc`)

### Summary
`TextFormat::Printer::PrintAny()` writes the raw, attacker-controlled `type_url` string field of a `google.protobuf.Any` submessage directly into `ABSL_LOG(WARNING)` calls with no escaping of control characters (newlines, quotes, etc.). This is the same class of bug as CVE-2024-52337 in Tuned's `instance_create()`: an unescaped attacker-controlled string is written into an application/administrator-facing log stream, allowing a crafted value to inject fake log lines or otherwise mislead anyone reading the logs.

### Finding Description
When a message contains a `google.protobuf.Any` field, `TextFormat::Printer::Print()`/`PrintMessage()` calls `PrintAny()` if `expand_any_` is enabled (this is set by internal helpers like `internal::StringifyMessage`, which is used to implement `DebugString()`/`ShortDebugString()`/`Utf8DebugString()` style debug printing) [1](#0-0) . Inside `PrintAny()`, the `type_url` value is read straight out of the message via reflection:

```cpp
const std::string& type_url = reflection->GetString(message, type_url_field);
``` [2](#0-1) 

This value is entirely attacker-controlled: it comes from a `string` field inside an `Any` submessage that was populated by parsing an ordinary, bounded binary-encoded protobuf message via the standard `ParseFrom*`/wire-format API. No schema, registry, or application logic constrains its byte content — it can contain newlines, quotes, or any other printable/control characters.

When the type can't be resolved in the type registry, or when the embedded bytes fail to parse as that type, the raw `type_url` is concatenated straight into a log line with no escaping:

```cpp
if (value_descriptor == nullptr) {
  ABSL_LOG(WARNING) << "Can't print proto content: proto type " << type_url
                    << " not found";
  return false;
}
...
if (!value_message->ParseFromString(serialized_value)) {
  ABSL_LOG(WARNING) << type_url << ": failed to parse contents";
  return false;
}
``` [3](#0-2) 

This is exactly the invariant broken in the Tuned CVE: a string value obtained from an untrusted/attacker-controlled input is inserted verbatim into a log stream that is expected to represent single, well-formed log records. Contrast this with the rest of `TextFormat`'s printing code, which is careful to escape field *values* before embedding them in output — e.g. unknown fields and normal string fields go through `absl::CEscape()`/`TextFormatEscaper` before being written [4](#0-3) . The `type_url` in `PrintAny()`'s error paths is the one place where a raw, unescaped, attacker-influenced string reaches `ABSL_LOG` directly.

### Impact Explanation
An attacker who controls the bytes of a protobuf message that a victim application later logs/debug-prints (a very common pattern — logging request/response protos via `DebugString()`/`ShortDebugString()` for diagnostics) can inject a `type_url` value containing embedded newlines and text crafted to resemble a legitimate, different log line (e.g., mimicking timestamps, severity markers, or other log fields). This can be used to:
- Forge fake log entries (log injection/spoofing), misleading administrators or automated log-parsing/alerting systems.
- Obscure the attacker's own actions in the log stream by pushing spoofed lines that hide or bury the real warning.

This does not lead to memory corruption or RCE; it is a log-integrity issue (matches the "improper output neutralization for logs" class), consistent with the Medium severity of the analog CVE-2024-52337 (C:N/I:H/A:N).

### Likelihood Explanation
Reachability requires: (1) the consuming application to enable `expand_any_` when text-formatting/debug-printing a message (true for `DebugString()`/`Utf8DebugString()`-style helpers, which set `SetExpandAny(true)`), and (2) the message to contain an `Any` field whose `type_url` cannot be resolved to a registered type, or whose embedded `value` fails to parse as that type — both of which an attacker fully controls simply by crafting the `type_url` string and/or `value` bytes in an otherwise valid, bounded binary payload. Given how routinely applications log incoming protobuf messages for debugging, this is a realistically triggerable, low-complexity path requiring no privileged access.

### Recommendation
Escape `type_url` (e.g., with `absl::CEscape` or the same `TextFormatEscaper`/`CEscape` machinery used elsewhere in `text_format.cc`) before including it in the `ABSL_LOG(WARNING)` calls in `PrintAny()`, or omit the raw value from the log line entirely (e.g., log only a length/hash, or quote-and-escape it) to prevent control characters from being interpreted as line breaks/formatting by log consumers.

### Proof of Concept
Conceptual reproduction (bounded, trusted-schema, standard binary parse API):
1. Construct a message `M` containing a `google.protobuf.Any` field `any`.
2. Set `any.type_url` to a crafted string containing an embedded newline plus a forged log-like suffix, e.g.:
   `"type.googleapis.com/Bogus\nW20260101 00:00:00 admin.cc:1] fake trusted log line"`
3. Serialize `M` to bytes and have the victim parse it via the normal binary `ParseFromString`/`ParseFromArray` API (bounded, valid wire format, trusted schema/generated code) — this is a completely ordinary, successful parse.
4. Victim application later calls `M.DebugString()` (or equivalent that sets `expand_any_ = true`) for logging/diagnostics.
5. Because the type registry cannot resolve `"Bogus"`, `PrintAny()` executes `ABSL_LOG(WARNING) << "Can't print proto content: proto type " << type_url << " not found";`, which writes the embedded newline and the forged text directly into the log stream, producing what appears to be a second, legitimate-looking log entry.

I was not able to locate a full end-to-end test harness in the indexed portion of the repository that actually invokes `ABSL_LOG` sinks and captures output, so step 5's exact log-formatting behavior (timestamp/line prefixing behavior of `ABSL_LOG`) could not be directly executed and confirmed in this environment; this should be validated by a Devin session with terminal access.

### Citations

**File:** src/google/protobuf/text_format.cc (L2536-2545)
```text
  const Reflection* reflection = message.GetReflection();

  // Extract the full type name from the type_url field.
  const std::string& type_url = reflection->GetString(message, type_url_field);
  std::string url_prefix;
  std::string full_type_name;

  if (!internal::ParseAnyTypeUrl(type_url, &url_prefix, &full_type_name)) {
    return false;
  }
```

**File:** src/google/protobuf/text_format.cc (L2551-2563)
```text
  if (value_descriptor == nullptr) {
    ABSL_LOG(WARNING) << "Can't print proto content: proto type " << type_url
                      << " not found";
    return false;
  }
  DynamicMessageFactory factory;
  std::unique_ptr<Message> value_message(
      factory.GetPrototype(value_descriptor)->New());
  std::string serialized_value = reflection->GetString(message, value_field);
  if (!value_message->ParseFromString(serialized_value)) {
    ABSL_LOG(WARNING) << type_url << ": failed to parse contents";
    return false;
  }
```

**File:** src/google/protobuf/text_format.cc (L2601-2609)
```text
void TextFormat::Printer::PrintMessage(const Message& message,
                                       BaseTextGenerator* generator) const {
  if (generator == nullptr || generator->failed()) {
    return;
  }
  const Descriptor* descriptor = message.GetDescriptor();
  if (descriptor->full_name() == internal::kAnyFullTypeName && expand_any_ &&
      PrintAny(message, generator)) {
    return;
```

**File:** src/google/protobuf/text_format.cc (L3175-3181)
```text
          generator->PrintMaybeWithMarker(MarkerToken(), ": ", "\"");
          generator->PrintString(absl::CEscape(value));
          if (single_line_mode_) {
            generator->PrintLiteral("\" ");
          } else {
            generator->PrintLiteral("\"\n");
          }
```
