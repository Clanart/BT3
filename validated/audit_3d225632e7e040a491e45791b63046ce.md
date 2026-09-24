This is a critical finding: `PrintOptions::allow_legacy_nonconformant_behavior` **defaults to `true`** [1](#0-0) , meaning the vulnerable code path in `WriteFieldMask` is active by default for any caller using `MessageToJsonString`/`MessageToJsonStream` without explicitly overriding this option.

### Title
JSON injection via unescaped characters in `google.protobuf.FieldMask` path serialization when `allow_legacy_nonconformant_behavior` is enabled (default) - (`src/google/protobuf/json/internal/unparser.cc`)

### Summary
`WriteFieldMask()` serializes each character of a `FieldMask.paths` string (a `repeated string`, fully attacker-controlled via binary wire input) directly into the JSON output buffer using `writer.Write(c)`, bypassing the library's normal JSON string escaper (`WriteEscapedUtf8`/`MakeQuoted`). When the default `allow_legacy_nonconformant_behavior = true` option is in effect, any character that is not lowercase/digit/`.`/`_` (including `"`, `\`, and control characters) is written to the output **raw and unescaped**, allowing the attacker to break out of the JSON string literal and inject arbitrary JSON structure into the serialized output — directly analogous to the reported `VerbsToken.tokenURI()` JSON injection where an unsanitized string was concatenated into a JSON document.

### Finding Description
`MessageToJsonString`/`MessageToJsonStream` (the public ProtoJSON serialization API) call into `json_internal::WriteFieldMask` whenever a message contains a `google.protobuf.FieldMask` field [2](#0-1) . The FieldMask's `paths` field is an ordinary `repeated string` populated straight from the parsed binary wire input — fully attacker-controlled.

`WriteFieldMask` writes the opening quote, then for each character of each path applies a hand-rolled snake_case→camelCase transform: [3](#0-2) 

The critical bug is in the fallback branches (lines 748–760): when `allow_legacy_nonconformant_behavior` is true (the default, per `PrintOptions::allow_legacy_nonconformant_behavior = true` at [1](#0-0) ), any character that is not lowercase, a digit, `.`, or the first `_` in a run is written **verbatim** via `writer.Write(c)` at line 759, with no call to `WriteEscapedUtf8`/`MustEscape` (the mechanism every other string field in the codebase uses, e.g., [4](#0-3) ). This means `"`, `\`, `\n`, control characters, etc. inside a `FieldMask` path are copied into the middle of a JSON string literal without escaping.

Comparing to every other string-typed field/value in the same file (`WriteAny`'s `type_url`, generic string fields via `MakeQuoted`, map keys via `WriteMapKey`) — all of these route through `JsonWriter::WriteQuoted`/`WriteEscapedUtf8`, which does correctly escape `"`, `\`, control chars, etc. `WriteFieldMask` is the sole exception, manually iterating characters and writing them directly to the stream.

This transfers the exact invariant failure from the report: the `CultureIndex`/`VerbsToken` bug was "attacker-controlled string is embedded into a JSON document without escaping quote/structural characters," letting the attacker terminate the current JSON string/object and inject new key-value pairs. The `FieldMask` path exhibits the identical missing-escape defect on a supported, non-legacy, non-test production path (`MessageToJsonString`).

### Impact Explanation
Any application that:
1. Accepts a `FieldMask`-containing protobuf message from an untrusted binary source (a common, supported use case — FieldMask is frequently used in update/patch RPCs), and
2. Re-serializes that message to JSON via `MessageToJsonString`/`BinaryToJsonString` for logging, forwarding to a downstream JSON consumer, embedding in an HTTP response, etc.

is exposed to attacker-controlled JSON structure injection. An attacker can supply a `paths` entry such as `"a\", \"injected\": \"x"` (with the disallowed characters like `"`—only reachable because `allow_legacy_nonconformant_behavior` defaults to true) to append arbitrary keys/values to the emitted JSON object, or truncate/redirect fields a downstream consumer trusts (mirroring the "replace image after voting" attack — here, "inject fields the consuming application did not intend to be present"). Because `allow_legacy_nonconformant_behavior` defaults to `true`, this is reachable with **zero special configuration** by the calling application, elevating likelihood significantly above a typical opt-in legacy-flag bug.

Severity: Medium/High depending on how the JSON is consumed downstream (matches the C4 judge's reasoning that JSON structural injection into a document trusted by another system component is a protocol-level integrity failure, not merely front-end QA).

### Likelihood Explanation
High for reachability: `MessageToJsonString`/`MessageToJsonStream` are the standard, documented, public ProtoJSON APIs; `FieldMask` is a standard well-known type; `allow_legacy_nonconformant_behavior` is `true` by default (an explicit `PrintOptions()` override is required to disable it) [1](#0-0) . No malformed schema, no privileged access, and no huge/unbounded input is required — a single short `paths` string with an embedded quote character suffices.

### Recommendation
Route the `FieldMask` path-character loop through the same escaping primitive (`WriteEscapedUtf8`) used by all other string output paths, instead of `writer.Write(c)`, regardless of `allow_legacy_nonconformant_behavior`. At minimum, when characters outside the allowed FieldMask charset are encountered in "legacy" mode, they must still be passed through JSON escaping before being written, never emitted raw. Consider changing the default of `allow_legacy_nonconformant_behavior` to `false` for `PrintOptions` (as the code comments already say is the long-term intent) to eliminate this unescaped fallback entirely.

### Proof of Concept
Construct a `google.protobuf.FieldMask` (or an application message embedding one) with:
```
paths: ["a\", \"injected_by_attacker\": \"pwned"]
```
Serialize it via `google::protobuf::json::MessageToJsonString(message, &output, PrintOptions())` (default options, so `allow_legacy_nonconformant_behavior == true`). Tracing `WriteFieldMask`: the initial `"` is written, then characters `a` (lowercase, written raw — fine), then `\"` — a `"` character. This character is not lowercase/digit/`.`/`_`, and since `allow_legacy_nonconformant_behavior` is true, control falls to the `else` branch at line 755-759 and the raw `"` is written directly to the stream via `writer.Write(c)`, terminating the JSON string prematurely and injecting the following `, "injected_by_attacker": "pwned` text as literal JSON structure into the output — with no sanitizer call anywhere in the loop. (I was not able to execute this against the actual build environment in this session — I confirmed the code path and defaults via static reading of `unparser.cc`, `json.h`, and `json.cc`; a background agent with build tooling should compile and run this as an actual unit test/conformance addition to obtain concrete captured output bytes.)

### Citations

**File:** src/google/protobuf/json/json.h (L43-47)
```text
struct PrintOptions {
  // Whether some legacy non-spec behaviors are accepted for bug
  // compatibility reasons. Setting allow_legacy_nonconformant_behavior=false is
  // recommended for new code and is expected to eventually become the default.
  bool allow_legacy_nonconformant_behavior = true;
```

**File:** src/google/protobuf/json/json.cc (L96-111)
```text
absl::Status MessageToJsonStream(const Message& message,
                                 io::ZeroCopyOutputStream* json_output,
                                 const PrintOptions& options) {
  google::protobuf::json_internal::WriterOptions opts;
  opts.add_whitespace = options.add_whitespace;
  opts.preserve_proto_field_names = options.preserve_proto_field_names;
  opts.always_print_enums_as_ints = options.always_print_enums_as_ints;
  opts.always_print_fields_with_no_presence =
      options.always_print_fields_with_no_presence;
  opts.unquote_int64_if_possible = options.unquote_int64_if_possible;

  // TODO: Drop this setting.
  opts.allow_legacy_nonconformant_behavior =
      options.allow_legacy_nonconformant_behavior;

  return google::protobuf::json_internal::MessageToJsonStream(message, json_output, opts);
```

**File:** src/google/protobuf/json/internal/unparser.cc (L729-767)
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
        writer.Write(c);
      } else if (c == '_' &&
                 (!saw_under ||
                  writer.options().allow_legacy_nonconformant_behavior)) {
        saw_under = true;
        continue;
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
  }
  writer.Write('"');

  return absl::OkStatus();
}
```

**File:** src/google/protobuf/json/internal/writer.cc (L284-323)
```text
void JsonWriter::WriteEscapedUtf8(absl::string_view str) {
  while (!str.empty()) {
    static constexpr absl::CharSet kUnsafeChars =
        ~absl::CharSet::AsciiPrintable() | absl::CharSet("\"\\<>");

    auto [safe_prefix, rest] = SplitAtFirst(str, kUnsafeChars);
    if (!safe_prefix.empty()) {
      Write(safe_prefix);
      str = rest;
      if (str.empty()) {
        break;
      }
    }

    auto scalar = ConsumeUtf8Scalar(str);
    absl::string_view custom_escape;

    if (!MustEscape(scalar.u32, custom_escape)) {
      Write(scalar.utf8);
      continue;
    }

    if (!custom_escape.empty()) {
      Write(custom_escape);
      continue;
    }

    if (scalar.u32 < 0x10000) {
      WriteUEscape(scalar.u32);
      continue;
    }

    uint16_t lo =
        (scalar.u32 & (kMaxLowSurrogate - kMinLowSurrogate)) + kMinLowSurrogate;
    uint16_t hi = (scalar.u32 >> 10) +
                  (kMinHighSurrogate - (kMinSupplementaryCodePoint >> 10));
    WriteUEscape(hi);
    WriteUEscape(lo);
  }
}
```
