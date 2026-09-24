Confirmed: `PrintOptions::allow_legacy_nonconformant_behavior` defaults to `true` in this checkout [1](#0-0) , so the vulnerable branch in `WriteFieldMask` is reachable by default through the public `MessageToJsonString`/`MessageToJsonStream` API.

### Title
FieldMask path characters are written unescaped into ProtoJSON string literals, allowing JSON structure injection - (File: src/google/protobuf/json/internal/unparser.cc)

### Summary
The vertx-core advisory (CVE-2018-12537) failed to filter CR/LF from attacker-controlled values before embedding them into a delimiter/structure-sensitive text protocol (HTTP headers), letting a client inject new header fields. `WriteFieldMask()` in the C++ ProtoJSON writer has the same class of defect: when serializing a `google.protobuf.FieldMask.paths` string to JSON in "legacy nonconformant" mode (the default), it writes path bytes into the surrounded `"..."` JSON string literal via raw `writer.Write(c)` calls instead of routing them through the string-escaping path (`WriteEscapedUtf8`/`MustEscape`) used everywhere else in the codebase.

### Finding Description
`FieldMask.paths` is an ordinary `repeated string` field with no character restriction enforced at binary-parse time [2](#0-1) , so an attacker fully controls its bytes via a bounded, valid binary Protobuf payload (e.g. embedding a `FieldMask` message, or an `Any` containing one).

When the message is serialized to ProtoJSON with `MessageToJsonString`, `WriteFieldMask` opens a quoted JSON string with `writer.Write('"')` and then iterates each path character [3](#0-2) . Characters that are lowercase/digit/`.` are copied verbatim; `_` triggers camelCase conversion. Any other character (e.g. `"`, `\`, `\n`, `\r`, control bytes) hits the final `else` branch, which is reachable only when `allow_legacy_nonconformant_behavior` is true — the checked-out default [1](#0-0)  — and simply calls `writer.Write(c)` [4](#0-3) .

Crucially, `JsonWriter::Write(char c)` performs a raw byte append with **no escaping** [5](#0-4) . Every other string-producing path in the same writer (`WriteEscapedUtf8`, `MustEscape`) explicitly escapes `"`, `\`, and control characters including `\n`/`\r` before writing them into a JSON string literal [6](#0-5) . `WriteFieldMask` bypasses that invariant entirely for non-conforming characters.

This is the direct structural analog of the vertx bug: a value that is supposed to be safely embedded inside a delimited/quoted container (HTTP header line / JSON string) contains the container's own control character (CRLF / `"`), and the missing filter lets that character terminate the container early and inject new structure — a forged HTTP header there, forged JSON keys/values here.

### Impact Explanation
If a path contains a `"`, the emitted JSON becomes structurally broken/injectable: e.g. a path of `foo","injected":"x` produces `"foo","injected":"x"` inside the FieldMask's JSON string context, which — depending on how the surrounding JSON object is assembled by the writer — can inject a sibling key/value pair into the enclosing JSON object literal. Any downstream consumer that treats the resulting text as trusted, well-formed JSON (logging pipelines, JSON-based ACL/update-mask decisions, cross-service JSON forwarding) can be misled into observing a different (attacker-chosen) JSON document than the sender intended, an integrity failure with the same shape as HTTP header/response splitting. It also breaks round-trip parsing (the resulting document may fail to parse, or reparse into a different `FieldMask`/object) — availability/integrity impact scoped to Medium, consistent with the report's Medium severity and matching the "preserve eligible Medium" guidance.

### Likelihood Explanation
- The vulnerable branch is default-enabled (`allow_legacy_nonconformant_behavior = true` by default in this checkout) [1](#0-0) .
- `FieldMask.paths` is a plain string with no schema-level character restriction, fully attacker-controllable via a valid, bounded binary payload.
- The only requirement is that a service parse attacker-supplied binary Protobuf containing a `FieldMask` (or an `Any` wrapping one) and then re-serialize it to ProtoJSON — a common pattern for logging, gRPC-Gateway transcoding, or update-mask echoing.

### Recommendation
Route the non-conforming character branch in `WriteFieldMask` (unparser.cc, lines 755-760) through the same escaping primitive used for ordinary strings (`WriteEscapedUtf8`/`MustEscape`) instead of `writer.Write(c)`, so that `"`, `\`, and control characters are always escaped regardless of `allow_legacy_nonconformant_behavior`. Consider also defaulting `allow_legacy_nonconformant_behavior` to `false` for `PrintOptions`, per the code's own comment that this is the recommended direction [7](#0-6) .

### Proof of Concept
1. Construct a `FieldMask` message with `paths = ["a\",\"x\":\"1"]` (i.e., a path containing a literal `"` followed by `,"x":"1`) and serialize it to binary Protobuf (trusted schema, valid wire format, bounded size).
2. Feed the bytes to a service that calls `google::protobuf::json::MessageToJsonString` (default `PrintOptions`, i.e., `allow_legacy_nonconformant_behavior=true`) on a message embedding this `FieldMask`.
3. In `WriteFieldMask`, the `"` and subsequent characters fail the lowercase/digit/`.`/`_` checks and, because `allow_legacy_nonconformant_behavior` is true, are written raw via `writer.Write(c)` [4](#0-3) , producing output such as `"fieldMask":"a","x":"1"` instead of a single escaped string value `"fieldMask":"a\",\"x\":\"1"` — a structural break/injection into the surrounding JSON object.

I was not able to execute this locally against the built library (no execution environment available here); the trace above is based on static analysis of the writer/option-default code paths. A background Devin session with build/test tooling could run the exact `MessageToJsonString` call above and diff the resulting bytes against a JSON parser to confirm the injected key, if further confirmation is desired.

### Citations

**File:** src/google/protobuf/json/json.h (L43-47)
```text
struct PrintOptions {
  // Whether some legacy non-spec behaviors are accepted for bug
  // compatibility reasons. Setting allow_legacy_nonconformant_behavior=false is
  // recommended for new code and is expected to eventually become the default.
  bool allow_legacy_nonconformant_behavior = true;
```

**File:** src/google/protobuf/any.proto (L72-76)
```text
message Any {
  // Identifies the type of the serialized Protobuf message with a URI reference
  // consisting of a prefix ending in a slash and the fully-qualified type name.
  //
  // Example: type.googleapis.com/google.protobuf.StringValue
```

**File:** src/google/protobuf/json/internal/unparser.cc (L729-746)
```text
template <typename Traits>
absl::Status WriteFieldMask(JsonWriter& writer, const Msg<Traits>& msg,
                            const Desc<Traits>& desc) {
  // google.protobuf.FieldMask has a single field with number 1.
  auto paths_field = Traits::MustHaveField(desc, 1);
  size_t paths = Traits::GetSize(paths_field, msg);
  writer.Write('"');

  bool first = true;
  for (size_t i = 0; i < paths; ++i) {
    writer.WriteComma(first);
    auto path = Traits::GetString(paths_field, writer.ScratchBuf(), msg, i);
    RETURN_IF_ERROR(path.status());
    bool saw_under = false;
    for (char c : *path) {
      if (absl::ascii_islower(c) && saw_under) {
        writer.Write(absl::ascii_toupper(c));
      } else if (absl::ascii_isdigit(c) || absl::ascii_islower(c) || c == '.') {
```

**File:** src/google/protobuf/json/internal/unparser.cc (L753-762)
```text
      } else if (!writer.options().allow_legacy_nonconformant_behavior) {
        return absl::InvalidArgumentError("unexpected character in FieldMask");
      } else {
        if (saw_under) {
          writer.Write('_');
        }
        writer.Write(c);
      }
      saw_under = false;
    }
```

**File:** src/google/protobuf/json/internal/writer.h (L100-102)
```text
  void Write(absl::string_view str) { sink_.Append(str.data(), str.size()); }

  void Write(char c) { sink_.Append(&c, 1); }
```

**File:** src/google/protobuf/json/internal/writer.cc (L198-222)
```text
static bool MustEscape(uint32_t scalar, absl::string_view& custom_escape) {
  switch (scalar) {
    // These escapes are defined by the JSON spec. We do not escape /.
    case '\n':
      custom_escape = R"(\n)";
      return true;
    case '\r':
      custom_escape = R"(\r)";
      return true;
    case '\t':
      custom_escape = R"(\t)";
      return true;
    case '\"':
      custom_escape = R"(\")";
      return true;
    case '\f':
      custom_escape = R"(\f)";
      return true;
    case '\b':
      custom_escape = R"(\b)";
      return true;
    case '\\':
      custom_escape = R"(\\)";
      return true;

```
