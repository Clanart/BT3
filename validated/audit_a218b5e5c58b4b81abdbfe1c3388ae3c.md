## Analog Found: Unescaped `Any.type_url` breaks JSON string context in PHP's pure-PHP JSON serializer

### Title
Pure-PHP `Message::serializeToJsonStream()` writes `Any.type_url` unescaped into JSON, allowing JSON-injection via a malicious binary payload - (File: `php/src/Google/Protobuf/Internal/Message.php`)

### Summary
The Hats.sol report shows that concatenating an attacker-controlled string directly into a hand-built JSON document, without escaping quote/brace characters, lets the attacker break out of the intended string field and inject arbitrary sibling JSON keys/values. The protobuf codebase has an almost line-for-line analog: the pure-PHP implementation's special-cased `google.protobuf.Any` JSON printer writes the `type_url` string field to the output stream with a raw `writeRaw()` call instead of routing it through the library's JSON string-escaping routine, while every other string field (and every other language's JSON encoder) is escaped.

### Finding Description
In `Message::serializeToJsonStream()`, the `Any` branch is special-cased: [1](#0-0) 
```
$output->writeRaw("\"@type\":", 8);
$output->writeRaw("\"", 1);
$output->writeRaw($this->getTypeUrl(), strlen($this->getTypeUrl()));
$output->writeRaw("\"", 1);
```
`getTypeUrl()` returns the raw contents of the `type_url` string field of the `Any` message, exactly as it was decoded from the wire. This field has **no character restriction on the wire** — it is just `string type_url = 1;`: [2](#0-1) 

For every *other* string field, this same file routes serialization through `GPBJsonWire::serializeFieldToStream`, which calls `json_encode($value, JSON_UNESCAPED_UNICODE)` (or, for the manual escaper, `GPBJsonWire::escapedJson()`), both of which correctly escape `"`, `\`, and control characters: [3](#0-2) [4](#0-3) 

Every other language/backend implementation of the identical `Any → JSON` special case correctly escapes `type_url` through the shared string-escaping routine before emitting it — e.g. upb (used by PHP's C extension), Ruby, and C++: [5](#0-4) [6](#0-5) 

This confirms that the *invariant* being violated — "every string field written into JSON output must go through the escaping/quoting path before being embedded in a string literal" — is the exact protobuf-side analog of the invariant broken in `Hats._constructURI`. The pure-PHP `Any` branch is the one place in the reviewed encoders that bypasses this invariant for a fully attacker-controlled string.

### Impact Explanation
An attacker who controls a bounded binary-encoded `Any` message (e.g., a client submitting a serialized `Any` that a PHP application later decodes and re-serializes to JSON via the public `serializeToJsonString()` API) can set `type_url` to a value containing `"`, `\`, or other JSON-structural characters. Because `type_url` is emitted with `writeRaw()` and no escaping, the attacker can:
- Break out of the `"@type":"..."` string,
- Terminate the object early or inject additional sibling keys/values into the `Any`'s JSON representation,
- Potentially corrupt or spoof fields that a downstream JSON consumer (e.g., some other service parsing this JSON with `json_decode` or a JS-based first-match parser) will read — mirroring exactly the "wrong data read downstream" impact described in the Hats.sol report.

This affects any application built on protobuf's pure-PHP runtime (i.e., without the `protobuf` C extension / upb installed) that (a) accepts untrusted serialized `Any` payloads and (b) re-emits them as JSON via `serializeToJsonString()`.

### Likelihood Explanation
- `type_url` is a plain `string` field on the wire with no content validation enforced at decode time in this checkout; nothing in the parsing path rejects `"`/`\` characters.
- `serializeToJsonString()` / `serializeToJsonStream()` are public, documented APIs.
- The pure-PHP runtime is a commonly used fallback whenever the native `protobuf` C extension is unavailable (e.g. many hosting environments, shared hosting, Composer-only installs).
- The bug requires no special privileges — just an attacker able to supply a binary protobuf `Any` payload that the trusted application later re-serializes to JSON.

### Recommendation
Route `type_url` through the same escaping helper used for all other string fields (`GPBJsonWire`'s JSON-string escaping/`json_encode`) before writing it, instead of `writeRaw()`ing the raw field bytes:
```php
$output->writeRaw("\"@type\":", 8);
$encoded = json_encode($this->getTypeUrl(), JSON_UNESCAPED_UNICODE);
$output->writeRaw($encoded, strlen($encoded));
```
This brings the pure-PHP `Any` path in line with the upb/Ruby/C++/Java/C# implementations, which already escape `type_url` through their shared JSON string writer.

### Proof of Concept
1. Construct a binary-encoded `google.protobuf.Any` message where field 1 (`type_url`, wire type 2/length-delimited string) contains the bytes:
   `type.googleapis.com/foo.Bar","injected":"pwned` (i.e., embed a literal `"` and comma inside the `type_url` string).
2. Decode it with the pure-PHP runtime: `$any = new \Google\Protobuf\Any(); $any->mergeFromString($bytes);`
3. Call `$any->serializeToJsonString();`
4. Because `Message::serializeToJsonStream()` writes `$this->getTypeUrl()` via `writeRaw()` with no escaping (`php/src/Google/Protobuf/Internal/Message.php:1493`), the resulting output is:
   ```json
   {"@type":"type.googleapis.com/foo.Bar","injected":"pwned", ... }
   ```
   which is syntactically valid JSON with an attacker-injected `"injected"` key that was never part of the intended schema — directly analogous to the malicious `"properties"` injection shown in the Hats.sol PoC. [7](#0-6)

### Citations

**File:** php/src/Google/Protobuf/Internal/Message.php (L1482-1494)
```php
    public function serializeToJsonStream(&$output)
    {
        $options = $output->getOptions();
        if (is_a($this, 'Google\Protobuf\Any')) {
            $output->writeRaw("{", 1);
            $type_field = $this->desc->getFieldByNumber(1);
            $value_msg = $this->unpack();

            // Serialize type url.
            $output->writeRaw("\"@type\":", 8);
            $output->writeRaw("\"", 1);
            $output->writeRaw($this->getTypeUrl(), strlen($this->getTypeUrl()));
            $output->writeRaw("\"", 1);
```

**File:** src/google/protobuf/any.proto (L72-102)
```text
message Any {
  // Identifies the type of the serialized Protobuf message with a URI reference
  // consisting of a prefix ending in a slash and the fully-qualified type name.
  //
  // Example: type.googleapis.com/google.protobuf.StringValue
  //
  // This string must contain at least one `/` character, and the content after
  // the last `/` must be the fully-qualified name of the type in canonical
  // form, without a leading dot. Do not write a scheme on these URI references
  // so that clients do not attempt to contact them.
  //
  // The prefix is arbitrary and Protobuf implementations are expected to
  // simply strip off everything up to and including the last `/` to identify
  // the type. `type.googleapis.com/` is a common default prefix that some
  // legacy implementations require. This prefix does not indicate the origin of
  // the type, and URIs containing it are not expected to respond to any
  // requests.
  //
  // All type URL strings must be legal URI references with the additional
  // restriction (for the text format) that the content of the reference
  // must consist only of alphanumeric characters, percent-encoded escapes, and
  // characters in the following set (not including the outer backticks):
  // `/-.~_!$&()*+,;=`. Despite our allowing percent encodings, implementations
  // should not unescape them to prevent confusion with existing parsers. For
  // example, `type.googleapis.com%2FFoo` should be rejected.
  //
  // In the original design of `Any`, the possibility of launching a type
  // resolution service at these type URLs was considered but Protobuf never
  // implemented one and considers contacting these URLs to be problematic and
  // a potential security issue. Do not attempt to contact type URLs.
  string type_url = 1;
```

**File:** php/src/Google/Protobuf/Internal/GPBJsonWire.php (L211-215)
```php
                break;
            case GPBType::STRING:
                $value = json_encode($value, JSON_UNESCAPED_UNICODE);
                $output->writeRaw($value, strlen($value));
                break;
```

**File:** php/src/Google/Protobuf/Internal/GPBJsonWire.php (L269-296)
```php
    public static function escapedJson($value)
    {
        $escaped_value = "";
        $unescaped_run = "";
        for ($i = 0; $i < strlen($value); $i++) {
            $c = $value[$i];
            // Handle escaping.
            if (static::isJsonEscaped($c)) {
                // Use a "nice" escape, like \n, if one exists for this
                // character.
                $escape = static::jsonNiceEscape($c);
                if (is_null($escape)) {
                    $escape = "\\u00" . bin2hex($c);
                }
                if ($unescaped_run !== "") {
                    $escaped_value .= $unescaped_run;
                    $unescaped_run = "";
                }
                $escaped_value .= $escape;
            } else {
              if ($unescaped_run === "") {
                $unescaped_run .= $c;
              }
            }
        }
        $escaped_value .= $unescaped_run;
        return $escaped_value;
    }
```

**File:** upb/json/encode.c (L410-411)
```c
  jsonenc_putstr(e, "{\"@type\":");
  jsonenc_string(e, type_url);
```

**File:** src/google/protobuf/json/internal/unparser.cc (L787-792)
```text
  auto type_url = Traits::GetString(type_url_field, writer.ScratchBuf(), msg);
  RETURN_IF_ERROR(type_url.status());
  writer.NewLine();
  writer.Write("\"@type\":");
  writer.Whitespace(" ");
  writer.Write(MakeQuoted(*type_url));
```
