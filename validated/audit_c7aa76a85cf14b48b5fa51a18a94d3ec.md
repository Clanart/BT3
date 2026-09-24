Confirmed: `JsonWriter::Write(char c)` at [1](#0-0)  writes a raw, unescaped byte straight to the output sink. This is exactly the "sink that bypasses the validated-header/escaped-string helper" pattern seen in the undici advisory (`headers.push('content-type', body.type)` bypassing `isValidHeaderValue()`), and `WriteFieldMask` in `unparser.cc` calls this raw `Write(c)` path for FieldMask path bytes instead of going through `JsonWriter::WriteEscapedUtf8()` (the function every other string field uses, which implements `MustEscape()` for `"`, `\`, control chars, etc., per [2](#0-1) ).

### Title
JSON Injection via Unescaped FieldMask Path Characters in Legacy-Nonconformant Mode - (File: src/google/protobuf/json/internal/unparser.cc)

### Summary
`WriteFieldMask()` in the C++ ProtoJSON binary-to-JSON serializer converts `FieldMask.paths` strings to camelCase and writes them directly into the JSON output. When `WriterOptions.allow_legacy_nonconformant_behavior` is enabled, any byte that is not a strict snake_case character (lowercase letter, digit, `.`, `_`) is written verbatim via `writer.Write(c)` — the raw, unescaped write primitive — instead of through `JsonWriter::WriteEscapedUtf8()`/`MustEscape()`, which is the path every other string-typed field in this JSON writer uses to escape `"`, `\`, and control characters (`\n`, `\r`, etc.).

### Finding Description
Every other string-serialization sink in this codebase — `TextFormat::Printer::HardenedPrintString`/`FastFieldValuePrinter::PrintString` (C++), `text_encoding.CEscape` (Python), `TextFormatEscaper`/`JsonFormat` in Java, `jsonenc_put_escaped_char` in upb (used by PHP, Ruby), and `JsonWriter::WriteEscapedUtf8` (C++ JSON) — escapes quotes, backslashes and control characters (including `\r`/`\n`) before emitting attacker-controlled string content, confirmed at [3](#0-2) .

`WriteFieldMask` in `unparser.cc` is the one call site that deviates from this invariant. In its character loop:
```
} else {
  if (saw_under) writer.Write('_');
  writer.Write(c);
}
``` [4](#0-3) 
When `allow_legacy_nonconformant_behavior` is true (a documented `WriterOptions` flag defined at [5](#0-4) ), any character in a path string that is not lowercase/digit/`.`/valid-underscore — including `"`, `\`, `\n`, `\r`, `<`, `>` — is passed to this branch and written via the raw `JsonWriter::Write(char c)` primitive, which performs zero escaping (`sink_.Append(&c, 1)` at [1](#0-0) ). A `FieldMask.paths` string is an ordinary protobuf `string` field with no wire-level restriction on its byte content, so a binary-encoded message parsed via a trusted schema and a public parse API can carry a path value containing `"` or `\r\n` unrestricted. Converting that message to JSON with legacy-nonconformant mode enabled then emits those raw bytes into the surrounding `"..."` JSON string literal, terminating it early and letting subsequent path bytes act as raw JSON syntax — a structural JSON-injection identical in mechanism to the undici CRLF-into-header injection (attacker-controlled value bypasses the mandatory validation/escaping routine used everywhere else in the same output pipeline).

### Impact Explanation
Any consuming application that: (1) accepts untrusted binary protobuf containing a `FieldMask` (directly, or nested via `Any`/embedded message), (2) re-serializes it to ProtoJSON using this C++ JSON writer with `allow_legacy_nonconformant_behavior=true`, and (3) treats the resulting text as JSON downstream, is exposed to injection of arbitrary sibling JSON keys/values or premature termination of the current string — potentially smuggling extra fields into a JSON document that another system trusts (e.g., overriding a later field, injecting a spoofed `"@type"` or authorization-adjacent key). This is a data/structural integrity issue on the serialization boundary, matching CWE-93/similar injection class; it does not by itself cause memory corruption or code execution.

### Likelihood Explanation
The path requires `allow_legacy_nonconformant_behavior` to be explicitly enabled by the calling application — this is an opt-in compatibility flag, not the default (`false` per `WriterOptions`), which limits exposure to legacy-mode consumers. However, once enabled, the trigger is trivial: any single `"` or `\` byte in a `FieldMask.paths` entry from an attacker-controlled binary payload is sufficient, requiring no other conditions, races, or size limits — fully deterministic and reachable via a single call to `MessageToJsonString`/equivalent public API on a decoded message containing a `FieldMask`.

### Recommendation
Route the fallback character(s) in `WriteFieldMask`'s legacy-nonconformant branch through the same escaping primitive used elsewhere (`JsonWriter::WriteEscapedUtf8` / the character-level `MustEscape` logic in `writer.cc`) rather than the raw `Write(char)` primitive, so that `"`, `\`, and control characters in FieldMask paths are always escaped regardless of the `allow_legacy_nonconformant_behavior` setting.

### Proof of Concept
1. Construct (with a trusted `.proto` schema referencing `google.protobuf.FieldMask`, or a message embedding one) a binary payload whose `FieldMask.paths` repeated string field contains the literal byte sequence `foo","injected":"bar` (i.e., an embedded `"` followed by additional JSON-looking text).
2. Parse this binary payload with the standard public API (`FieldMask::ParseFromString`/`Message::ParseFromArray` for a containing message) — no schema/API constraint rejects this value since `paths` is an unconstrained `string` field at the wire level.
3. Serialize the parsed message to ProtoJSON via `google::protobuf::json::MessageToJsonString` (or the internal `WriteFieldMask` path) with `JsonPrintOptions`/`WriterOptions.allow_legacy_nonconformant_behavior = true`.
4. Observe the produced JSON contains an unescaped `"` from the path value, terminating the FieldMask JSON string early and injecting `,"injected":"bar` as sibling JSON content — confirmed purely from source inspection of `WriteFieldMask` ( [6](#0-5) ) versus the escaping performed by `WriteEscapedUtf8` for ordinary string fields ( [7](#0-6) ). I was not able to execute this compiled reproduction in this environment (no build/test execution available here); this assessment is based on static code tracing of the cited functions, and confirming this at runtime would require a Devin session with build tooling.

### Citations

**File:** src/google/protobuf/json/internal/writer.h (L49-55)
```text
  // The original parser used by json_util2 accepted a number of non-standard
  // options. Setting this flag enables them.
  //
  // What those extensions were is explicitly not documented, beyond what exists
  // in the unit tests; we intend to remove this setting eventually. See
  // b/234868512.
  bool allow_legacy_nonconformant_behavior = false;
```

**File:** src/google/protobuf/json/internal/writer.h (L100-102)
```text
  void Write(absl::string_view str) { sink_.Append(str.data(), str.size()); }

  void Write(char c) { sink_.Append(&c, 1); }
```

**File:** src/google/protobuf/json/internal/writer.cc (L194-269)
```text
// Decides whether we must escape `scalar`.
//
// If the given Unicode scalar would not use a \u escape, `custom_escape` will
// be set to a non-empty string.
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

    case kErrorSentinel:
      // Decoding failure turns into spaces, *not* replacement characters. We
      // handle this separately from "normal" spaces so that it follows the
      // escaping code-path.
      //
      // Note that literal replacement characters in the input string DO NOT
      // get turned into spaces; this is only for decoding failures!
      custom_escape = " ";
      return true;

    // These are not required by the JSON spec, but help
    // to prevent security bugs in JavaScript.
    //
    // These were originally present in the ESF parser, so they are kept for
    // legacy compatibility (and because escaping most of these is in good
    // taste, regardless).
    case '<':
    case '>':
    case 0xfeff:      // Zero width no-break space.
    case 0xfff9:      // Interlinear annotation anchor.
    case 0xfffa:      // Interlinear annotation separator.
    case 0xfffb:      // Interlinear annotation terminator.
    case 0x00ad:      // Soft-hyphen.
    case 0x06dd:      // Arabic end of ayah.
    case 0x070f:      // Syriac abbreviation mark.
    case 0x17b4:      // Khmer vowel inherent Aq.
    case 0x17b5:      // Khmer vowel inherent Aa.
    case 0x000e0001:  // Language tag.
      return true;
    default:
      static constexpr std::pair<uint32_t, uint32_t> kEscapedRanges[] = {
          {0x0000, 0x001f},          // ASCII control.
          {0x007f, 0x009f},          // High ASCII bytes.
          {0x0600, 0x0603},          // Arabic signs.
          {0x200b, 0x200f},          // Zero width etc.
          {0x2028, 0x202e},          // Separators etc.
          {0x2060, 0x2064},          // Invisible etc.
          {0x206a, 0x206f},          // Shaping etc.
          {0x0001d173, 0x0001d17a},  // Music formatting.
          {0x000e0020, 0x000e007f},  // TAG symbols.
      };

      return absl::c_any_of(kEscapedRanges, [scalar](auto range) {
        return range.first <= scalar && scalar <= range.second;
      });
  }
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
