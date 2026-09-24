Confirmed: PHP's pure implementation (`GPBJsonWire.php:213`, `serializeSingularFieldValueToStream` for `GPBType::STRING`) uses plain `json_encode($value, JSON_UNESCAPED_UNICODE)`, and the PHP/Ruby upb-based native encoders (`jsonenc_put_escaped_char` in `php-upb.c:6807` and `ruby-upb.c`) only implement the RFC 4627-mandated escapes (`\n \r \t \" \\ \b \f` and control chars), with no additional escaping of `<`, `>`, `&`, `=`, `'`. This is in direct contrast to the Java (`JsonFormat.java:1583-1591`) and core C++ (`json/internal/writer.cc:233-251`) JSON writers, which explicitly escape these characters "to prevent security bugs in JavaScript" / "XSS risks" as defense-in-depth, citing this exact rationale in their source comments.

<br>

### Title
Inconsistent JSON string escaping across Protobuf language implementations omits HTML/JS-context defense-in-depth escaping (PHP/Ruby/upb) present in Java/C++ - (File: `php/src/Google/Protobuf/Internal/GPBJsonWire.php`, `php/ext/google/protobuf/php-upb.c`, `ruby/ext/google/protobuf_c/ruby-upb.c`)

### Summary
Protobuf's JSON serializers for PHP (both the pure-PHP `GPBJsonWire` writer and the native `upb`-based extension) and Ruby (native `upb`-based extension) emit attacker-controlled `string`/enum-name field values into JSON output using only the minimal JSON-spec-mandated escapes. They omit the additional escaping of `<`, `>`, `&`, `=`, `'`, and various Unicode "invisible"/separator code points that the Java (`com.google.protobuf.util.JsonFormat`) and core C++ (`google::protobuf::json`) implementations perform explicitly and intentionally as a defense-in-depth measure against downstream HTML/JavaScript injection when JSON output is later embedded in an HTML or `<script>` context by a consuming application.

### Finding Description
`java/util/src/main/java/com/google/protobuf/util/JsonFormat.java:1583-1591` explicitly escapes `<>&='` "to prevent XSS risks", and this is verified by `java/util/src/test/java/com/google/protobuf/util/JsonFormatTest.java:2016-2035` (`testHtmlEscape`, regression test for b/73832901). The core C++ JSON writer at `src/google/protobuf/json/internal/writer.cc:233-251` (`MustEscape`) implements the identical set of extra escapes, with a comment stating they exist "to prevent security bugs in JavaScript," carried over from the legacy ESF parser. [1](#0-0) [2](#0-1) 

By contrast, the `upb`-based JSON encoder shared by the PHP and Ruby native extensions only escapes the RFC-4627-mandated set (`\n \r \t \" \\ \b \f` and control chars < 0x20), with no handling of `<`, `>`, `&`, `=`, `'`, or the extra Unicode ranges: [3](#0-2) [4](#0-3) [5](#0-4) 

The pure-PHP fallback writer (`GPBJsonWire.php`) delegates string and enum-name escaping directly to PHP's native `json_encode()` with only `JSON_UNESCAPED_UNICODE`, which by default does not escape `<`, `>`, `&`, `'`, `=` (PHP requires explicit `JSON_HEX_TAG`/`JSON_HEX_AMP`/`JSON_HEX_APOS` flags for that, which are not set here): [6](#0-5) 

An attacker who controls a Protobuf message's string field (or a custom JSON enum-value name) sent through the public `MessageToJsonString`/`serializeToJsonString` API in PHP or Ruby can therefore embed literal `</script>`, `<img onerror=...>`-style substrings, or other HTML-significant characters into the JSON output byte-for-byte, whereas the same message serialized via Java's `JsonFormat` or C++'s `google::protobuf::json` API would have those characters transformed into harmless `\u003c`/`\u003e` escapes.

### Impact Explanation
This is a cross-implementation hardening gap rather than a wire-format or memory-safety bug: no JSON syntax is broken and no data is corrupted (RFC 4627 does not require escaping these characters). The impact is exposure-dependent: a consuming application that embeds Protobuf-generated JSON directly into an HTML document or inline `<script>` block (a documented anti-pattern that Java/C++ explicitly harden against, per the `b/73832901` regression test) is protected when using Java or C++ bindings but not when using the PHP or Ruby bindings, reproducing the same class of failure as the Swift advisory: attacker-supplied content reaches a structured-output serializer without the escaping needed for the context it will ultimately be interpreted in, and only some of the affected serializers apply that escaping.

### Likelihood Explanation
High likelihood of reachability: any ordinary client can set an arbitrary UTF-8 string field (e.g., `optional_string`) or trigger a custom JSON enum-value name containing `<`, `>`, `&`, or `'`, then call the public `MessageToJsonString`/`JsonEncode` API in PHP or Ruby. No privileged access, malformed wire data, or schema tampering is required — this is a pure serialization-formatting gap in a fully supported, documented public API path.

### Recommendation
Apply the same `MustEscape`-style character set (`<`, `>`, `&`, `=`, `'`, plus the documented Unicode separator/invisible ranges) used in `src/google/protobuf/json/internal/writer.cc` and `JsonFormat.java` to the `upb` JSON encoder's `jsonenc_put_escaped_char`/`jsonenc_stringbody` path (shared by PHP and Ruby), and update `GPBJsonWire.php`'s `json_encode()` calls to add `JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS` (or perform the equivalent manual substitution) so behavior is consistent across all supported language bindings.

### Proof of Concept
Given proto `message M { string s = 1; }`:
1. In Ruby or PHP (native extension), set `s = "</script><script>alert(1)</script>"` and call `M.encode_json(msg)` / `$msg->serializeToJsonString()`.
   - Output: `{"s":"</script><script>alert(1)</script>"}` — literal `<`/`>` preserved, verifiable against `jsonenc_put_escaped_char` in `upb/json/encode.c:268-301` / `php-upb.c:6807-6840`, which has no `case '<':`/`case '>':` branch.
2. Using the same string value in Java via `JsonFormat.printer().print(msg)`:
   - Output: `{"s":"\u003c/script\u003e\u003cscript\u003ealert(1)\u003c/script\u003e"}` — per `testHtmlEscape` at `java/util/src/test/java/com/google/protobuf/util/JsonFormatTest.java:2016-2035`, confirming the divergence in escaping behavior between the two officially supported bindings for identical attacker-controlled input.

### Citations

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L1583-1591)
```java
      // These characters are fully legal in JSON, but are escaped to prevent XSS risks. Notably
      // this topic is not like 'html escaping' where these would be replaced with something like
      // `&lt;`. The escaped or not ways of writing it are verbatim 2 exactly equivalent
      // ways to represent the same exact value in JSON: consumers should never do any manual
      // unescape to round trip the intended value. This replacement is only be semantically
      // observable if someone tries to handle it raw textually and not as JSON.
      for (int i : "<>&='".toCharArray()) {
        replacementChars[i] = String.format("\\u%04x", i);
      }
```

**File:** src/google/protobuf/json/internal/writer.cc (L233-251)
```text
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
```

**File:** upb/json/encode.c (L268-301)
```c
static void jsonenc_put_escaped_char(jsonenc* e, char ch) {
  switch (ch) {
    case '\n':
      jsonenc_putstr(e, "\\n");
      break;
    case '\r':
      jsonenc_putstr(e, "\\r");
      break;
    case '\t':
      jsonenc_putstr(e, "\\t");
      break;
    case '\"':
      jsonenc_putstr(e, "\\\"");
      break;
    case '\f':
      jsonenc_putstr(e, "\\f");
      break;
    case '\b':
      jsonenc_putstr(e, "\\b");
      break;
    case '\\':
      jsonenc_putstr(e, "\\\\");
      break;
    default:
      if ((uint8_t)ch < 0x20) {
        jsonenc_printf(e, "\\u%04x", (int)(uint8_t)ch);
      } else {
        /* This could be a non-ASCII byte.  We rely on the string being valid
         * UTF-8. */
        jsonenc_putbytes(e, &ch, 1);
      }
      break;
  }
}
```

**File:** php/ext/google/protobuf/php-upb.c (L6807-6840)
```c
static void jsonenc_put_escaped_char(jsonenc* e, char ch) {
  switch (ch) {
    case '\n':
      jsonenc_putstr(e, "\\n");
      break;
    case '\r':
      jsonenc_putstr(e, "\\r");
      break;
    case '\t':
      jsonenc_putstr(e, "\\t");
      break;
    case '\"':
      jsonenc_putstr(e, "\\\"");
      break;
    case '\f':
      jsonenc_putstr(e, "\\f");
      break;
    case '\b':
      jsonenc_putstr(e, "\\b");
      break;
    case '\\':
      jsonenc_putstr(e, "\\\\");
      break;
    default:
      if ((uint8_t)ch < 0x20) {
        jsonenc_printf(e, "\\u%04x", (int)(uint8_t)ch);
      } else {
        /* This could be a non-ASCII byte.  We rely on the string being valid
         * UTF-8. */
        jsonenc_putbytes(e, &ch, 1);
      }
      break;
  }
}
```

**File:** ruby/ext/google/protobuf_c/ruby-upb.c (L5595-5603)
```c
static void jsonenc_stringbody(jsonenc* e, upb_StringView str) {
  const char* ptr = str.data;
  const char* end = UPB_PTRADD(ptr, str.size);

  while (ptr < end) {
    jsonenc_put_escaped_char(e, *ptr);
    ptr++;
  }
}
```

**File:** php/src/Google/Protobuf/Internal/GPBJsonWire.php (L188-215)
```php
                $enum_value_desc = $enum_desc->getValueByNumber($value);
                if (!is_null($enum_value_desc)) {
                    $str_value = json_encode(
                        self::formatEnumValueName($enum_value_desc),
                        JSON_UNESCAPED_UNICODE);
                    $output->writeRaw($str_value, strlen($str_value));
                } else {
                    $str_value = strval($value);
                    $output->writeRaw($str_value, strlen($str_value));
                }
                break;
            case GPBType::BOOL:
                if ($value) {
                    $output->writeRaw("true", 4);
                } else {
                    $output->writeRaw("false", 5);
                }
                break;
            case GPBType::BYTES:
                $bytes_value = base64_encode($value);
                $output->writeRaw("\"", 1);
                $output->writeRaw($bytes_value, strlen($bytes_value));
                $output->writeRaw("\"", 1);
                break;
            case GPBType::STRING:
                $value = json_encode($value, JSON_UNESCAPED_UNICODE);
                $output->writeRaw($value, strlen($value));
                break;
```
