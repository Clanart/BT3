## Analysis

The Electron advisory's core invariant is: **a security-relevant string comparison/validation performed on the full byte-length of an attacker-controlled string must match the actual length used by the consuming API**. `shell.openPath()` failed this invariant because Node's string handling let embedded `\0` truncate the path when it reached the underlying OS call, while application-level validation (extension checks, etc.) had already run against the *full* string.

Protobuf's PHP extension has the exact same class of bug in `DescriptorPool::getDescriptorByProtoName()`.

### Finding [1](#0-0) 

```c
PHP_METHOD(DescriptorPool, getDescriptorByProtoName) {
  DescriptorPool* intern = GetPool(getThis());
  char* protoname = NULL;
  zend_long protoname_len;
  const upb_MessageDef* m;

  if (zend_parse_parameters(ZEND_NUM_ARGS(), "s", &protoname, &protoname_len) ==
      FAILURE) {
    return;
  }

  if (*protoname == '.') protoname++;

  m = upb_DefPool_FindMessageByName(intern->symtab, protoname);
  ...
}
```

`zend_parse_parameters("s", ...)` correctly returns the true byte length of the PHP string in `protoname_len` (PHP strings are binary-safe and can contain embedded `\0` bytes), but that length is **never used**. The lookup calls the non-size-aware variant: [2](#0-1) 

```c
const upb_MessageDef* upb_DefPool_FindMessageByName(const upb_DefPool* s,
                                                    const char* sym) {
  return upb_DefPool_FindMessageByNameWithSize(s, sym, strlen(sym));
}
```

which internally calls `strlen(sym)`, silently truncating at the first `\0` — a byte that a PHP application can freely embed anywhere in a string it constructs (e.g. derived from an `Any.type_url` after unpacking, or from other attacker-influenced data), while any application-level allow/deny-list check written in PHP (`str_starts_with`, `in_array`, prefix/suffix comparisons, etc.) operates on the full binary string including everything after the `\0`.

This is precisely the bug class protobuf's own Python binding was hardened against and explicitly regression-tested for: [3](#0-2) 

```python
def testFindNULLByte(self, normal_name, method_name):
    # Attack: Craft malicious type name with embedded null byte
    malicious_name = normal_name + '\x00Tail.Content.Ignore'
    ...
    with self.assertRaises(KeyError) as exc:
      method(malicious_name)
```

The Python C extension avoids the bug because it always passes an explicit size to `upb_DefPool_FindMessageByNameWithSize`: [4](#0-3) 

The PHP extension has no equivalent hardening or test for `getDescriptorByProtoName`.

### Title
PHP `DescriptorPool::getDescriptorByProtoName()` truncates type names at embedded NUL byte, causing lookup/validation mismatch - (`File: php/ext/google/protobuf/def.c`)

### Summary
`DescriptorPool::getDescriptorByProtoName()` accepts a binary-safe PHP string (which may legitimately contain `\0`) but discards the parsed length and passes the string to `upb_DefPool_FindMessageByName()`, which resolves the symbol using `strlen()`. Any bytes in the input after the first `\0` are silently ignored by the lookup, while PHP-level string operations the calling application performs on the same variable (e.g. allow-list/prefix checks) see the full string. This produces a “string validated against value A, but engine resolves value B” mismatch, the same class of check-bypass as Electron's `shell.openPath()` NUL-byte issue.

### Finding Description
`zend_parse_parameters(ZEND_NUM_ARGS(), "s", &protoname, &protoname_len)` obtains a binary-safe C buffer and its true byte length in `protoname_len`. The method then calls `upb_DefPool_FindMessageByName(intern->symtab, protoname)`, which internally recomputes the length via `strlen(protoname)` rather than using `protoname_len`. `upb`'s underlying `upb_strtable_lookup`/`upb_strtable_lookup2` machinery is fully binary-safe and size-aware (see `upb/hash/str_table.h`), so the deficiency is solely in the PHP wrapper's choice to call the size-agnostic `FindMessageByName` instead of `FindMessageByNameWithSize` (which the PHP `Any::unpack()`/`Any::is()` code paths in `message.c` correctly use via `upb_DefPool_FindMessageByNameWithSize(symtab, type_url.data, type_url.size)`). This is an inconsistency within the same extension: one call site is hardened, the other is not.

### Impact Explanation
A consuming PHP application that builds a fully-qualified type name from untrusted/attacker-influenced input (for example, deriving a candidate type name string from a client-supplied `Any.type_url`, an API parameter, or a JSON/text field, and performing its own string-based authorization/allow-list check such as “must equal one of these known-safe type names” or “must start with an allowed package prefix”) before calling `$pool->getDescriptorByProtoName($name)` can be bypassed: an attacker appends `\0` + arbitrary suffix bytes so the *full* string passes the app's allow-list check (or fails a deny-list check) while the engine actually resolves the *prefix before the NUL* to a different, unintended message descriptor. This can let an attacker obtain a `Descriptor` for — and subsequently instantiate/parse into — a message type the application did not intend to allow, which is an integrity/confidentiality-relevant type-confusion analogous to the “open unintended file” impact in the Electron report.

### Likelihood Explanation
Requires an application built on protobuf's PHP runtime to (a) construct a proto type-name string containing attacker-controlled content and (b) perform validation via native PHP string operations on that string prior to calling `getDescriptorByProtoName()`, without independently validating for embedded NUL bytes (mirroring the Electron advisory's own stated affected-usage precondition). This is a plausible but not universal application pattern; PHP's Protobuf ecosystem commonly funnels type resolution through `Any::unpack()`, which is not affected (it correctly uses the size-aware variant). Given the narrower blast radius (only this one public API is affected, not the primary binary/JSON parse path), severity is assessed as Medium, consistent with the source advisory’s own Medium rating and its analogous “apps that rely on string-only validation without a size/NUL check” precondition.

### Recommendation
Change `php/ext/google/protobuf/def.c`'s `getDescriptorByProtoName` (and audit sibling `DescriptorPool::find*ByProtoName`-style methods for the same pattern) to call `upb_DefPool_FindMessageByNameWithSize(intern->symtab, protoname, protoname_len)`, using the length already returned by `zend_parse_parameters`, instead of the NUL-terminated `upb_DefPool_FindMessageByName`. Additionally, consider rejecting names containing embedded NUL bytes outright (returning `NULL`/throwing), matching the explicit regression test added for the Python binding (`testFindNULLByte`).

### Proof of Concept
Conceptual PHP repro (illustrates the mismatch; requires the PHP protobuf extension with a message named `Foo.Safe` registered and no `Foo.Evil` registered, or vice versa, to demonstrate the bypass):
```php
$pool = \Google\Protobuf\Internal\DescriptorPool::getGeneratedPool();

// Application-level allow-list check operates on the FULL binary string.
$candidate = "Foo.Safe" . "\0" . "Foo.Evil";
if (in_array($candidate, ["Foo.Safe"], true)) {
    // Never true here since $candidate != "Foo.Safe" as a full byte string,
    // demonstrating the check sees the whole buffer...
}

// ...but the native lookup truncates at '\0' and resolves "Foo.Safe" regardless
// of what the app's string check decided, because getDescriptorByProtoName()
// internally calls strlen() instead of using the length PHP already knows.
$desc = $pool->getDescriptorByProtoName($candidate);
// $desc resolves to "Foo.Safe" even though the literal string doesn't match
// any single entry an application might have explicitly allow-listed,
// or conversely bypasses a deny-list entry for "Foo.Safe\0Foo.Evil".
```
This mirrors the Electron PoC pattern (`path + '\0' + otherPath` bypassing extension checks) applied to protobuf's PHP type-resolution API instead of a filesystem path.

### Citations

**File:** php/ext/google/protobuf/def.c (L868-888)
```c
PHP_METHOD(DescriptorPool, getDescriptorByProtoName) {
  DescriptorPool* intern = GetPool(getThis());
  char* protoname = NULL;
  zend_long protoname_len;
  const upb_MessageDef* m;

  if (zend_parse_parameters(ZEND_NUM_ARGS(), "s", &protoname, &protoname_len) ==
      FAILURE) {
    return;
  }

  if (*protoname == '.') protoname++;

  m = upb_DefPool_FindMessageByName(intern->symtab, protoname);

  if (m) {
    RETURN_OBJ_COPY(&Descriptor_GetFromMessageDef(m)->std);
  } else {
    RETURN_NULL();
  }
}
```

**File:** upb/reflection/def_pool.c (L231-234)
```c
const upb_MessageDef* upb_DefPool_FindMessageByName(const upb_DefPool* s,
                                                    const char* sym) {
  return upb_DefPool_FindMessageByNameWithSize(s, sym, strlen(sym));
}
```

**File:** python/google/protobuf/internal/descriptor_pool_test.py (L1960-1972)
```python
  def testFindNULLByte(self, normal_name, method_name):
    # Attack: Craft malicious type name with embedded null byte
    malicious_name = normal_name + '\x00Tail.Content.Ignore'
    method = getattr(descriptor_pool.Default(), method_name)
    des = method(normal_name)

    if hasattr(des, 'full_name'):
      self.assertEqual(normal_name, des.full_name)
    else:
      self.assertEqual(normal_name, des.name)
    with self.assertRaises(KeyError) as exc:
      method(malicious_name)
    self.assertIn('Tail.Content', str(exc.exception))
```

**File:** python/descriptor_pool.c (L505-524)
```c
static PyObject* PyUpb_DescriptorPool_FindMessageTypeByName(PyObject* _self,
                                                            PyObject* arg) {
  PyUpb_DescriptorPool* self = (PyUpb_DescriptorPool*)_self;

  const char* name = PyUpb_VerifyStrData(arg);
  if (!name) return NULL;
  Py_ssize_t name_size = PyObject_Size(arg);

  const upb_MessageDef* m =
      upb_DefPool_FindMessageByNameWithSize(self->symtab, name, name_size);
  if (m == NULL && self->db) {
    if (!PyUpb_DescriptorPool_TryLoadSymbol(self, arg)) return NULL;
    m = upb_DefPool_FindMessageByNameWithSize(self->symtab, name, name_size);
  }
  if (m == NULL) {
    return PyErr_Format(PyExc_KeyError, "Couldn't find message %S", arg);
  }

  return PyUpb_Descriptor_Get(_self, m);
}
```
