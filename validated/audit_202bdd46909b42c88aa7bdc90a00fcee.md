Confirmed: `ParseAnyTypeUrl` only requires a `/` at some position; everything before the last `/` (the `url_prefix`) is unrestricted, and the substring after it just needs to match a full type name known to the type registry/descriptor pool (a trusted, application-registered schema type — not attacker data). This means an attacker fully controls the `url_prefix` portion of `type_url` and can embed arbitrary bytes there (`]`, `{`, `}`, `#`, `\n`, `"`, etc.) as long as the string ends in `/<a real registered full_type_name>`. [1](#0-0) 

### Title
TextFormat `Any` expansion injects unescaped attacker-controlled `type_url` into printed output - (File: src/google/protobuf/text_format.cc)

### Summary
When `TextFormat::Printer` has `SetExpandAny(true)` set (or Java's `TextFormat.Printer.printAny`), the `type_url` string field of a `google.protobuf.Any` submessage — a value that is entirely attacker-controlled when a client sends bounded binary Protobuf through a public parse API — is written directly into the TextFormat output stream via `PrintString`/`print`, with none of the escaping (`CEscape`, quote/backslash/newline escaping) that every other string-valued field goes through elsewhere in the same printer.

### Finding Description
Protobuf's TextFormat writers meticulously escape all `STRING`/`BYTES` field values before embedding them in the textual output, in every language binding examined: C++ `FastFieldValuePrinter::PrintString` uses `absl::CEscape` when special characters are present, and the hardened path `HardenedPrintString` walks the buffer and escapes any byte for which `DefinitelyNeedsEscape` is true (quotes, backslash, control chars) before emitting it. [2](#0-1) [3](#0-2) 

Java's `TextFormat.printFieldValue` for `STRING` similarly always calls `TextFormatEscaper.escapeText`/`escapeDoubleQuotesAndBackslashes`. [4](#0-3) 

However, the special-case `Any` expansion path bypasses this escaping entirely. In C++, `TextFormat::Printer::PrintAny` writes the raw `type_url` string straight into the generator with `PrintString(type_url)`, bracketed only by literal `[` and `]`: [5](#0-4) 

The Java equivalent, `TextFormat.Printer.printAny`, does the same: `generator.print(typeUrl)` with no call to `TextFormatEscaper`. [6](#0-5) 

The only gate on this path is `internal::ParseAnyTypeUrl`, which merely requires a `/` somewhere in the string and treats everything after the final `/` as the type name to resolve against the (trusted) type registry; everything before that `/` — the `url_prefix` — is passed through unrestricted. [1](#0-0) 

So as long as an attacker constructs a message that (a) contains an `Any` submessage whose `value` bytes successfully parse as some type that is registered in the application's `TypeRegistry`/`ExtensionRegistry` (a normal, expected condition — not a hostile schema), and (b) sets `type_url` to `"<attacker-controlled-prefix>/<real.registered.TypeName>"`, the attacker fully controls the prefix bytes that get embedded verbatim, unescaped, into the printed TextFormat output between `[` and `]`.

This is the direct analog of the WeasyPrint flaw: an attacker-controlled string (`background` attribute / `type_url` prefix) is concatenated unescaped into a syntax-significant position of a format that is designed to be re-parsed (CSS / TextFormat), allowing the attacker to break out of the intended lexical context and inject additional syntax.

### Impact Explanation
An attacker-controlled `type_url` prefix can contain `]`, `{`, `}`, `"`, `#` (TextFormat comment marker), and newlines. If the resulting TextFormat text is subsequently consumed by any downstream process that treats it as TextFormat to be re-parsed (`TextFormat.merge`/`TextFormat::Parser::Parse` — a common pattern for logging pipelines, debug consoles, or serialization round-trips that log-and-replay via TextFormat), the attacker can:
- Prematurely close the extension bracket (`]`) and inject fabricated field assignments into the surrounding message text.
- Inject a `#` to comment out subsequent legitimate fields, silently dropping data integrity (CWE-74 analog).
- Corrupt the structural integrity of debug/log output used for auditing or replay, which can translate into an integrity violation (`I:L` per the original CVSS) when that output is trusted and reprocessed.

This does not grant memory corruption or RCE — it's confined to text-format re-interpretation/injection, matching the Medium severity class of the source finding.

### Likelihood Explanation
The precondition — an application enabling `SetExpandAny(true)` (C++) or the equivalent default Any-expansion behavior in Java's `TextFormat.printer()`, combined with a `TypeRegistry` that includes at least one commonly-used well-known or application type (e.g., `google.protobuf.StringValue`, or any application message) — is a very common, default-adjacent configuration in services that log or debug protobuf messages via TextFormat. The attacker only needs to control the bytes of a `google.protobuf.Any.type_url` field inside an otherwise valid, bounded message sent through a normal binary-parse API; no privileged access or hostile schema is required.

### Recommendation
Route the `type_url` string through the same escaping function (`FastFieldValuePrinter::PrintString`/`CEscape`, or `TextFormatEscaper.escapeText` in Java) used for ordinary STRING fields before emitting it inside the `[...]` extension-name syntax, or reject/escape `type_url` values containing TextFormat-significant characters (`]`, `{`, `}`, `"`, `#`, control characters) prior to printing.

### Proof of Concept
Construct (conceptually, via the binary wire format) an `Any` message where:
- `value` is a valid serialization of a registered type, e.g., `google.protobuf.StringValue{ value: "x" }`.
- `type_url` = `` `evil] extra_field: "injected"  #/google.protobuf.StringValue` `` — i.e., a prefix ending in `/google.protobuf.StringValue` but containing `]`, whitespace, and a fabricated field assignment before the required suffix.

Feed this bounded, well-formed binary message through the ordinary public `ParseFromString`/`MergeFrom` API, then call `TextFormat::Printer` with `SetExpandAny(true)` (C++) or `TextFormat.printer()` (Java, default) on the parsed message. `ParseAnyTypeUrl` succeeds (the suffix after the last `/` matches the registered type name), `PrintAny` prints `[evil] extra_field: "injected"  #/google.protobuf.StringValue] { value: "x" }` verbatim, unescaped — demonstrating that attacker-controlled bytes reach the syntax-significant position of the TextFormat output. I was not able to execute this against a live build in this environment (read-only code index); the code paths cited above show the missing-escape condition is directly reachable from parsed message data via the public `Print`/`PrintToString` APIs. A background Devin session with a full checkout and build environment would be needed to compile and run this PoC end-to-end and confirm the exact printed bytes and any re-parse behavior.

### Citations

**File:** src/google/protobuf/any_lite.cc (L72-84)
```text
bool ParseAnyTypeUrl(absl::string_view type_url,
                     std::string* PROTOBUF_NULLABLE url_prefix,
                     std::string* PROTOBUF_NONNULL full_type_name) {
  size_t pos = type_url.find_last_of('/');
  if (pos == std::string::npos || pos + 1 == type_url.size()) {
    return false;
  }
  if (url_prefix) {
    *url_prefix = std::string(type_url.substr(0, pos + 1));
  }
  *full_type_name = std::string(type_url.substr(pos + 1));
  return true;
}
```

**File:** src/google/protobuf/text_format.cc (L1877-1900)
```text
void TextFormat::Printer::HardenedPrintString(
    absl::string_view src, TextFormat::BaseTextGenerator* generator) {
  // Print as UTF-8, while guarding against any invalid UTF-8 in the string
  // field.
  //
  // If in the future we have a guaranteed invariant that invalid UTF-8 will
  // never be present, we could avoid the UTF-8 check here.

  generator->PrintLiteral("\"");
  while (!src.empty()) {
    size_t n = SkipPassthroughBytes(src);
    if (n != 0) {
      generator->PrintString(src.substr(0, n));
      src.remove_prefix(n);
      if (src.empty()) break;
    }

    // If repeated calls to CEscape() and PrintString() are expensive, we could
    // consider batching them, at the cost of some complexity.
    generator->PrintString(absl::CEscape(src.substr(0, 1)));
    src.remove_prefix(1);
  }
  generator->PrintLiteral("\"");
}
```

**File:** src/google/protobuf/text_format.cc (L2227-2238)
```text
void TextFormat::FastFieldValuePrinter::PrintString(
    const std::string& val, BaseTextGenerator* generator) const {
  generator->PrintLiteral("\"");
  if (!val.empty()) {
    if (ABSL_PREDICT_FALSE(ContainsCharactersToCEscape(val))) {
      generator->PrintString(absl::CEscape(val));
    } else {
      generator->PrintString(val);
    }
  }
  generator->PrintLiteral("\"");
}
```

**File:** src/google/protobuf/text_format.cc (L2564-2566)
```text
  generator->PrintLiteral("[");
  generator->PrintString(type_url);
  generator->PrintLiteral("]");
```

**File:** java/core/src/main/java/com/google/protobuf/TextFormat.java (L459-461)
```java
      generator.print("[");
      generator.print(typeUrl);
      generator.print("]");
```

**File:** java/core/src/main/java/com/google/protobuf/TextFormat.java (L633-640)
```java
        case STRING:
          generator.print("\"");
          generator.print(
              escapeNonAscii
                  ? TextFormatEscaper.escapeText((String) value)
                  : escapeDoubleQuotesAndBackslashes((String) value).replace("\n", "\\n"));
          generator.print("\"");
          break;
```
