### Title
Missing HTML/script-context escaping (`<`, `>`) in upb's ProtoJSON string encoder allows script-tag breakout when reflected into HTML - (`upb/json/encode.c`)

### Summary
CVE-2017-6396 is a reflected-XSS bug class: a value the application receives is echoed back into an HTML response without HTML/script-context-aware escaping, letting an attacker break out with `</script>`/`<script>` sequences. Protobuf itself has no HTTP endpoint, but it does have a directly analogous "output encoding" surface: the ProtoJSON string writer, whose comments and tests explicitly acknowledge and defend against exactly this bug class ("these are not required by the JSON spec, but help to prevent security bugs in JavaScript"). That defense is implemented in the C++ core (`src/google/protobuf/json/internal/writer.cc`) and in Java (`java/util/src/main/java/com/google/protobuf/util/JsonFormat.java`), but the separate upb JSON encoder used by the Ruby and PHP native extensions does **not** implement it.

### Finding Description
The invariant that C++/Java-based ProtoJSON writers enforce is: any code point that could let an embedded JSON string escape out of a `<script>`/HTML context (`<`, `>`, plus a set of "invisible"/bidi-control Unicode code points) must be `\u`-escaped, even though this is not required by the JSON spec:

- C++: `MustEscape()` in `src/google/protobuf/json/internal/writer.cc:198-269` explicitly escapes `'<'`/`'>'` and code points like `0xfeff`, `0x2028-0x202e`, etc., with the comment "help to prevent security bugs in JavaScript" [1](#0-0) , and `WriteEscapedUtf8` treats `<`/`>` as "unsafe" characters requiring the scalar-escape path [2](#0-1) .
- Java: `JsonFormat.java` builds a `replacementChars` table that explicitly escapes `<>&='` "to prevent XSS risks", separate from normal JSON escaping [3](#0-2) . This is backed by a regression test explicitly labeled for HTML/script safety: `testHtmlEscape` ("Regression test for b/73832901. Make sure html tags are escaped") [4](#0-3) .
- C#: `JsonFormatter.cs` `WriteString` also escapes the same Unicode ranges via `HexEncodeUtf16CodeUnit` for zero-width/bidi characters [5](#0-4) .

In contrast, upb's JSON encoder — which backs the PHP and Ruby native-extension `serializeToJsonString`/`encode_json` public APIs — only escapes the standard JSON control characters (`\n \r \t \" \f \b \\` and low ASCII control bytes) and passes all other bytes (including `<` and `>`) straight through: [6](#0-5) 

This same `jsonenc_put_escaped_char`/`jsonenc_stringbody`/`jsonenc_string` sequence is compiled/vendored into both the Ruby extension (`ruby/ext/google/protobuf_c/ruby-upb.c:5560-5609`) and the PHP extension (`php/ext/google/protobuf/php-upb.c:6842-6856`), and is reached directly from the public `serializeToJsonString`/`encode_json` entry points: `Message_encode_json` in Ruby [7](#0-6)  and `Message::serializeToJsonString` in PHP [8](#0-7) .

The transferable invariant from the CVE: "user-controlled string data that will later be embedded in an HTML/`<script>` execution context must be encoded so that it cannot terminate that context." The Java/C++ implementations codify this invariant directly into the JSON writer (independent of what the JSON spec requires) precisely because ProtoJSON output is a common thing to embed inline in HTML (e.g., `<script>var x = {{json}};</script>` bootstrapping patterns). The upb encoder — used for Ruby and PHP — omits this hardening, so a string field value containing `</script><script>alert(1)</script>` (attacker-controlled, delivered either directly as a message field the application later serializes back to ProtoJSON, or via `mergeFromJsonString`/`decode_json` on attacker JSON and later re-serialization) will be emitted byte-for-byte, with `<`/`>` unescaped, in the PHP/Ruby ProtoJSON output.

### Impact Explanation
If a consuming application (using the Ruby or PHP protobuf bindings) takes a message containing an attacker-influenced string field and calls `serializeToJsonString`/`encode_json` to embed the result inline in an HTML page (a common server-side-rendering pattern for bootstrapping client-side state), the missing `<`/`>` escaping in upb's encoder means the resulting JSON can break out of a `<script>` block and inject arbitrary script — the same class of impact as CVE-2017-6396 (reflected XSS: C mid/limited depending on context, matching the CVSS 6.1 "changed scope" profile since it affects the rendering browser, not the server). This is scoped to consuming applications that embed ProtoJSON directly into HTML without their own escaping layer; Protobuf itself has no HTTP surface. This is the same caveat that applies to the original CVE (WebPageTest, a web app) — the "impact" here is on whatever application chooses to inline this output into HTML, which the Java/C++ hardening exists specifically to mitigate.

### Likelihood Explanation
Reachability is high and requires no privileged access: any caller of the standard public `serializeToJsonString` (PHP) / `encode_json` (Ruby) API with attacker-influenced string field data reaches the vulnerable path directly, with a bounded/normal-sized string payload — no huge-input or resource-exhaustion requirement. The precondition (the consuming application inlines ProtoJSON output into HTML without its own encoding) is exactly the scenario the existing Java/C++/C# hardening was added to defend against, indicating it's a recognized, real-world usage pattern for ProtoJSON. Likelihood is Medium: it depends on the consuming application's rendering pattern, which is outside Protobuf's control, but the missing defense-in-depth is squarely within Protobuf's JSON serialization code and is inconsistent across language backends of the same project.

### Recommendation
Port the `MustEscape`/`replacementChars` HTML-context defense from the C++ (`src/google/protobuf/json/internal/writer.cc`) and Java (`JsonFormat.java`) implementations into upb's `jsonenc_put_escaped_char`/`jsonenc_stringbody` (`upb/json/encode.c`), so that `<` and `>` (and ideally the same "invisible"/bidi Unicode ranges) are `\u`-escaped for all consumers of the shared upb JSON encoder (Ruby, PHP, and any other upb-based binding). Add a regression test mirroring Java's `testHtmlEscape` to `php/tests/EncodeDecodeTest.php` and the Ruby test suite to lock in parity.

### Proof of Concept
Code-level PoC (not executed; based on static tracing of the escaping tables above):
1. PHP: 
```php
$m = new TestMessage();
$m->setOptionalString("</script><script>alert(1)</script>");
echo $m->serializeToJsonString();
```
Expected (per upb encoder, `upb/json/encode.c:268-317`, no `<`/`>` case): output contains the literal, unescaped substring `</script><script>alert(1)</script>` inside the JSON string value.
2. Contrast with Java:
```java
TestAllTypes m = TestAllTypes.newBuilder().setOptionalString("</script>").build();
JsonFormat.printer().print(m);
```
Per `JsonFormatTest.testHtmlEscape` (lines 2016-2035), Java's output is `"optionalString": "\u003c/script\u003e"` — `<`/`>` are safely escaped.

This confirms the check exists in Java/C++ specifically to prevent this bug class, and is absent in the upb encoder shared by PHP/Ruby, which is the exploitable gap.

### Citations

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

**File:** src/google/protobuf/json/internal/writer.cc (L284-296)
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
```

**File:** java/util/src/main/java/com/google/protobuf/util/JsonFormat.java (L1583-1592)
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
    }
```

**File:** java/util/src/test/java/com/google/protobuf/util/JsonFormatTest.java (L2016-2035)
```java
  // Regression test for b/73832901. Make sure html tags are escaped.
  @Test
  public void testHtmlEscape() throws Exception {
    TestAllTypes message = TestAllTypes.newBuilder().setOptionalString("</script>").build();
    assertThat(toJsonString(message))
        .isEqualTo("{\n  \"optionalString\": \"\\u003c/script\\u003e\"\n}");

    TestAllTypes.Builder builder = TestAllTypes.newBuilder();
    JsonFormat.parser().merge(toJsonString(message), builder);
    assertThat(builder.getOptionalString()).isEqualTo(message.getOptionalString());

    // Explicitly test individual HTML unsafe characters to kill any negation mutants
    TestAllTypes message2 = TestAllTypes.newBuilder().setOptionalString("\n<>&='").build();
    assertThat(toJsonString(message2))
        .isEqualTo("{\n  \"optionalString\": \"\\n\\u003c\\u003e\\u0026\\u003d\\u0027\"\n}");

    TestAllTypes.Builder builder2 = TestAllTypes.newBuilder();
    JsonFormat.parser().merge(toJsonString(message2), builder2);
    assertThat(builder2.getOptionalString()).isEqualTo(message2.getOptionalString());
  }
```

**File:** csharp/src/Google.Protobuf/JsonFormatter.cs (L717-744)
```csharp
        switch ((uint)c) {
          // These are not required by json spec
          // but used to prevent security bugs in javascript.
          case 0xfeff:  // Zero width no-break space
          case 0xfff9:  // Interlinear annotation anchor
          case 0xfffa:  // Interlinear annotation separator
          case 0xfffb:  // Interlinear annotation terminator

          case 0x00ad:  // Soft-hyphen
          case 0x06dd:  // Arabic end of ayah
          case 0x070f:  // Syriac abbreviation mark
          case 0x17b4:  // Khmer vowel inherent Aq
          case 0x17b5:  // Khmer vowel inherent Aa
            HexEncodeUtf16CodeUnit(writer, c);
            break;

          default:
            if ((c >= 0x0600 && c <= 0x0603) ||  // Arabic signs
                (c >= 0x200b && c <= 0x200f) ||  // Zero width etc.
                (c >= 0x2028 && c <= 0x202e) ||  // Separators etc.
                (c >= 0x2060 && c <= 0x2064) ||  // Invisible etc.
                (c >= 0x206a && c <= 0x206f)) {
              HexEncodeUtf16CodeUnit(writer, c);
            } else {
              // No handling of surrogates here - that's done earlier
              writer.Write(c);
            }
            break;
```

**File:** upb/json/encode.c (L268-317)
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

static void jsonenc_stringbody(jsonenc* e, upb_StringView str) {
  const char* ptr = str.data;
  const char* end = UPB_PTRADD(ptr, str.size);

  while (ptr < end) {
    jsonenc_put_escaped_char(e, *ptr);
    ptr++;
  }
}

static void jsonenc_string(jsonenc* e, upb_StringView str) {
  jsonenc_putstr(e, "\"");
  jsonenc_stringbody(e, str);
  jsonenc_putstr(e, "\"");
}
```

**File:** ruby/ext/google/protobuf_c/message.c (L1195-1258)
```c
static VALUE Message_encode_json(int argc, VALUE* argv, VALUE klass) {
  Message* msg = ruby_to_Message(argv[0]);
  int options = 0;
  char buf[1024];
  size_t size;
  upb_Status status;

  if (argc < 1 || argc > 2) {
    rb_raise(rb_eArgError, "Expected 1 or 2 arguments.");
  }

  if (argc == 2) {
    VALUE hash_args = argv[1];
    if (TYPE(hash_args) != T_HASH) {
      if (RTEST(rb_funcall(hash_args, rb_intern("respond_to?"), 1,
                           rb_str_new2("to_h")))) {
        hash_args = rb_funcall(hash_args, rb_intern("to_h"), 0);
      } else {
        rb_raise(rb_eArgError, "Expected hash arguments.");
      }
    }

    if (RTEST(rb_hash_lookup2(hash_args,
                              ID2SYM(rb_intern("preserve_proto_fieldnames")),
                              Qfalse))) {
      options |= upb_JsonEncode_UseProtoNames;
    }

    if (RTEST(rb_hash_lookup2(hash_args, ID2SYM(rb_intern("emit_defaults")),
                              Qfalse))) {
      options |= upb_JsonEncode_EmitDefaults;
    }

    if (RTEST(rb_hash_lookup2(hash_args,
                              ID2SYM(rb_intern("format_enums_as_integers")),
                              Qfalse))) {
      options |= upb_JsonEncode_FormatEnumsAsIntegers;
    }
  }

  upb_Status_Clear(&status);
  const upb_DefPool* pool = upb_FileDef_Pool(upb_MessageDef_File(msg->msgdef));
  size = upb_JsonEncode(msg->msg, msg->msgdef, pool, options, buf, sizeof(buf),
                        &status);

  if (!upb_Status_IsOk(&status)) {
    rb_raise(cParseError, "Error occurred during encoding: %s",
             upb_Status_ErrorMessage(&status));
  }

  VALUE ret;
  if (size >= sizeof(buf)) {
    char* buf2 = malloc(size + 1);
    upb_JsonEncode(msg->msg, msg->msgdef, pool, options, buf2, size + 1,
                   &status);
    ret = rb_str_new(buf2, size);
    free(buf2);
  } else {
    ret = rb_str_new(buf, size);
  }

  rb_enc_associate(ret, rb_utf8_encoding());
  return ret;
}
```

**File:** php/ext/google/protobuf/message.c (L810-866)
```c
/**
 * Message::serializeToJsonString()
 *
 * Serializes this object to JSON.
 * @return string Serialized JSON data.
 */
PHP_METHOD(Message, serializeToJsonString) {
  Message* intern = (Message*)Z_OBJ_P(getThis());
  size_t size;
  int options = 0;
  char buf[1024];
  zval* flags = NULL;
  upb_Status status;

  if (zend_parse_parameters(ZEND_NUM_ARGS(), "|z", &flags) == FAILURE) {
    return;
  }

  if (flags == NULL || Z_TYPE_P(flags) == IS_FALSE) {
    // do nothing
  } else if (Z_TYPE_P(flags) == IS_TRUE) {
    options |= upb_JsonEncode_UseProtoNames;
  } else if (Z_TYPE_P(flags) == IS_LONG) {
    if (Z_LVAL_P(flags) & ALWAYS_PRINT_ENUMS_AS_INTS) {
      options |= upb_JsonEncode_FormatEnumsAsIntegers;
    }
    if (Z_LVAL_P(flags) & PRESERVE_PROTO_FIELD_NAMES) {
      options |= upb_JsonEncode_UseProtoNames;
    }
    if (Z_LVAL_P(flags) & EMIT_DEFAULTS) {
      options |= upb_JsonEncode_EmitDefaults;
    }
  }

  upb_Status_Clear(&status);
  size = upb_JsonEncode(intern->msg, intern->desc->msgdef,
                        DescriptorPool_GetSymbolTable(), options, buf,
                        sizeof(buf), &status);

  if (!upb_Status_IsOk(&status)) {
    zend_throw_exception_ex(NULL, 0,
                            "Error occurred during JSON serialization: %s",
                            upb_Status_ErrorMessage(&status));
    return;
  }

  if (size >= sizeof(buf)) {
    char* buf2 = malloc(size + 1);
    upb_JsonEncode(intern->msg, intern->desc->msgdef,
                   DescriptorPool_GetSymbolTable(), options, buf2, size + 1,
                   &status);
    RETVAL_STRINGL(buf2, size);
    free(buf2);
  } else {
    RETVAL_STRINGL(buf, size);
  }
}
```
