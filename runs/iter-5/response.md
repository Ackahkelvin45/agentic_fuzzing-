```python
import json
from hypothesis import strategies as st

# Core atomic strategies
json_chars = st.characters(blacklist_categories=('Cs',), blacklist_characters=None)
safe_string_chars = st.characters(blacklist_categories=('Cc', 'Cs'), whitelist_categories=('Ll', 'Lu', 'Lt', 'Lm', 'Lo', 'Nd', 'Nl', 'No', 'Pc', 'Pd', 'Ps', 'Pe', 'Pi', 'Pf', 'Po', 'Sm', 'Sc', 'Sk', 'So'))

# Escape sequences (valid and edge cases)
escapes = st.sampled_from(['\\"', '\\\\', '\\/', '\\b', '\\f', '\\n', '\\r', '\\t'])
unicode_escapes = st.sampled_from([
    '\\u0000', '\\u001F', '\\u007F', '\\u00FF', '\\u0100', '\\uFFFF',
    '\\uD800', '\\uDBFF', '\\uDC00', '\\uDFFF', '\\uD800\\uDC00',
    '\\u1234', '\\uABCD', '\\uabcd', '\\uAbCd'
])
all_escapes = st.one_of(escapes, unicode_escapes)

# String content: mix of safe chars, escapes, and raw control/NUL bytes
string_parts = st.one_of(
    st.text(alphabet=safe_string_chars, min_size=0, max_size=10),
    st.just(''),
    st.just('\x00'),
    st.just('\x01'),
    st.just('\x1F'),
    st.just('\n'),
    st.just('\r'),
    st.just('\t'),
    all_escapes,
    st.just('\\'),
    st.just('"'),
    st.just('\\u'),
)
string_content = st.lists(string_parts, min_size=0, max_size=8).map(''.join)
string_strategy = st.lists(
    st.tuples(st.just('"'), string_content, st.just('"')),
    min_size=1, max_size=1
).map(lambda x: x[0][0] + x[0][1] + x[0][2])

# Numbers: strict valid forms plus edge/modified variants
valid_int = st.one_of(
    st.just('0'),
    st.integers(min_value=1, max_value=999999).map(str),
)
valid_frac = st.one_of(st.just(''), st.lists(st.characters(min_codepoint=0x30, max_codepoint=0x39), min_size=1, max_size=6).map(lambda x: '.' + ''.join(x)))
valid_exp = st.one_of(
    st.just(''),
    st.lists(st.sampled_from(['e', 'E', 'e+', 'E+', 'e-', 'E-']), min_size=1, max_size=1).flatmap(lambda x: st.lists(st.characters(min_codepoint=0x30, max_codepoint=0x39), min_size=1, max_size=6).map(lambda y: x[0] + ''.join(y))),
)
valid_number = st.tuples(st.one_of(st.just(''), st.just('-')), valid_int, valid_frac, valid_exp).map(lambda t: t[0] + t[1] + t[2] + t[3])

# Edge number variants (some accepted, some rejected)
edge_numbers = st.one_of(
    st.just('1e309'),
    st.just('-1e309'),
    st.just('1e-309'),
    st.just('0e999'),
    st.just('-'),
    st.just('01'),
    st.just('+1'),
    st.just('.5'),
    st.just('1.'),
    st.just('1e'),
    st.just('1e+'),
    st.just('1e-'),
    st.just('0.0'),
    st.just('-0'),
    st.just('9e999999999999999999999'),
)
number_strategy = st.one_of(valid_number, edge_numbers)

# Simple values
true_val = st.just('true')
false_val = st.just('false')
null_val = st.just('null')

# Recursive value strategy - defined via composite for clarity
@st.composite
def value_strategy(draw):
    choice = draw(st.integers(min_value=0, max_value=5))
    if choice == 0:
        return draw(string_strategy)
    elif choice == 1:
        return draw(number_strategy)
    elif choice == 2:
        return draw(obj_strategy())
    elif choice == 3:
        return draw(arr_strategy())
    elif choice == 4:
        return draw(true_val)
    elif choice == 5:
        return draw(false_val)
    else:
        return draw(null_val)

# Object and array strategies (recursive)
@st.composite
def obj_strategy(draw):
    # Decide empty vs non-empty, bias toward non-empty
    if draw(st.booleans()) and draw(st.booleans()) and draw(st.booleans()) and draw(st.booleans()) and draw(st.booleans()) and draw(st.booleans()) and draw(st.booleans()) and draw(st.booleans()) and draw(st.booleans()):
        # Generate 1-5 pairs
        num_pairs = draw(st.integers(min_value=1, max_value=5))
        pairs = []
        for _ in range(num_pairs):
            key = draw(string_strategy)
            colon = draw(st.just(':'))
            val = draw(value_strategy())
            pairs.append(key + colon + val)
        # Possibly add trailing comma (accepted)
        trailing = draw(st.booleans()) and draw(st.booleans()) and draw(st.booleans()) and draw(st.booleans()) and draw(st.booleans())
        sep = ',' if trailing else ''
        return '{' + ','.join(pairs) + sep + '}'
    else:
        return '{}'

@st.composite
def arr_strategy(draw):
    if draw(st.booleans()) and draw(st.booleans()) and draw(st.booleans()) and draw(st.booleans()) and draw(st.booleans()) and draw(st.booleans()) and draw(st.booleans()):
        num_vals = draw(st.integers(min_value=1, max_value=5))
        vals = [draw(value_strategy()) for _ in range(num_vals)]
        trailing = draw(st.booleans()) and draw(st.booleans()) and draw(st.booleans()) and draw(st.booleans()) and draw(st.booleans())
        sep = ',' if trailing else ''
        return '[' + ','.join(vals) + sep + ']'
    else:
        return '[]'

# Near-valid malformed inputs
malformed = st.one_of(
    st.just('[,]'),
    st.just('[1,,2]'),
    st.just('{"a":1,,}'),
    st.just('{"a" 1}'),
    st.just('{"a":}'),
    st.just('{"a"}'),
    st.just('{"a":1,"b"}'),
    st.just('[1,2'),
    st.just('{"a":1'),
    st.just('"unterminated'),
    st.just('"bad\\x"'),
    st.just('{'),
    st.just('['),
    st.just(''),
    st.just(' '),
    st.just('tru'),
    st.just('nul'),
    st.just('fals'),
    st.just('truE'),
    st.just('True'),
    st.just('NaN'),
    st.just('Infinity'),
    st.just('-Infinity'),
    st.just('1 2'),
    st.just('true false'),
    st.just('[] extra'),
    st.just('{"a":1} trailing'),
    st.just('"\\uD800"'),
    st.just('"\\uDC00"'),
    st.just('"\\uZZZZ"'),
    st.just('"\\u123"'),
)

# Deep nesting (varying depth)
@st.composite
def deep_nested(draw):
    depth = draw(st.integers(min_value=0, max_value=8))
    # Build nested arrays/objects
    result = 'null'
    for _ in range(depth):
        if draw(st.booleans()):
            result = '[' + result + ']'
        else:
            result = '{"k":' + result + '}'
    return result

# Generate nesting toward cap (depth 1500-2048)
@st.composite
def generate_nesting_toward_cap(draw):
    depth = draw(st.integers(min_value=1500, max_value=2048))
    # Build nested arrays/objects
    result = 'null'
    for _ in range(depth):
        if draw(st.booleans()):
            result = '[' + result + ']'
        else:
            result = '{"k":' + result + '}'
    return result

# New strategy for diversified accepted structures: combine multiple values in arrays/objects with varied keys
@st.composite
def diversified_structures(draw):
    # Choose between a few structural patterns
    pattern = draw(st.integers(min_value=0, max_value=3))
    if pattern == 0:
        # Array of mixed values
        num_vals = draw(st.integers(min_value=1, max_value=8))
        vals = [draw(value_strategy()) for _ in range(num_vals)]
        return '[' + ','.join(vals) + ']'
    elif pattern == 1:
        # Object with multiple keys, some duplicate
        num_pairs = draw(st.integers(min_value=1, max_value=8))
        keys = [draw(st.one_of(string_strategy, st.just('"a"'), st.just('"b"'), st.just('"c"'))) for _ in range(num_pairs)]
        vals = [draw(value_strategy()) for _ in range(num_pairs)]
        pairs = [k + ':' + v for k, v in zip(keys, vals)]
        return '{' + ','.join(pairs) + '}'
    elif pattern == 2:
        # Nested structure with multiple levels, not too deep
        depth = draw(st.integers(min_value=1, max_value=4))
        result = draw(value_strategy())
        for _ in range(depth):
            if draw(st.booleans()):
                result = '[' + result + ',' + draw(value_strategy()) + ']'
            else:
                result = '{"k' + str(draw(st.integers(min_value=0, max_value=9))) + '":' + result + '}'
        return result
    else:
        # Array of arrays/objects
        num_vals = draw(st.integers(min_value=1, max_value=5))
        vals = []
        for _ in range(num_vals):
            if draw(st.booleans()):
                vals.append(draw(obj_strategy()))
            else:
                vals.append(draw(arr_strategy()))
        return '[' + ','.join(vals) + ']'

# New strategy for broader accepted structures: mix of all grammars with varied shapes
@st.composite
def broad_accepted_structures(draw):
    # Choose a pattern to diversify accepted structures
    pattern = draw(st.integers(min_value=0, max_value=4))
    if pattern == 0:
        # Nested arrays with varying mixed types
        depth = draw(st.integers(min_value=1, max_value=6))
        result = draw(value_strategy())
        for _ in range(depth):
            if draw(st.booleans()):
                result = '[' + result + ']'
            else:
                result = '{"key' + str(draw(st.integers(min_value=0, max_value=9))) + '":' + result + '}'
        return result
    elif pattern == 1:
        # Large flat array with many elements
        num_vals = draw(st.integers(min_value=10, max_value=20))
        vals = [draw(value_strategy()) for _ in range(num_vals)]
        return '[' + ','.join(vals) + ']'
    elif pattern == 2:
        # Large flat object with many keys
        num_pairs = draw(st.integers(min_value=10, max_value=20))
        pairs = []
        for i in range(num_pairs):
            key = draw(st.one_of(string_strategy, st.just('"key%d"' % i)))
            val = draw(value_strategy())
            pairs.append(key + ':' + val)
        return '{' + ','.join(pairs) + '}'
    elif pattern == 3:
        # Mixed array containing objects and arrays
        num_vals = draw(st.integers(min_value=5, max_value=10))
        vals = []
        for _ in range(num_vals):
            choice = draw(st.integers(min_value=0, max_value=2))
            if choice == 0:
                vals.append(draw(obj_strategy()))
            elif choice == 1:
                vals.append(draw(arr_strategy()))
            else:
                vals.append(draw(value_strategy()))
        return '[' + ','.join(vals) + ']'
    else:
        # Deeply nested mixed structure with many levels
        depth = draw(st.integers(min_value=5, max_value=10))
        result = 'null'
        for _ in range(depth):
            if draw(st.booleans()):
                result = '[' + result + ']'
            else:
                result = '{"k":' + result + '}'
        return result

# New strategy for even more diverse accepted structures: random keys, numbers, and nested combinations
@st.composite
def ultra_diverse(draw):
    # Choose from several patterns to maximize structural variety
    pattern = draw(st.integers(min_value=0, max_value=5))
    if pattern == 0:
        # Array with nested objects that have random key types
        num_objs = draw(st.integers(min_value=2, max_value=6))
        objs = []
        for _ in range(num_objs):
            num_pairs = draw(st.integers(min_value=1, max_value=4))
            pairs = []
            for _ in range(num_pairs):
                key = draw(st.one_of(string_strategy, st.just('""'), st.just('" "'), st.just('"\\u0000"')))
                val = draw(value_strategy())
                pairs.append(key + ':' + val)
            objs.append('{' + ','.join(pairs) + '}')
        return '[' + ','.join(objs) + ']'
    elif pattern == 1:
        # Object with mixed value types including large numbers
        num_pairs = draw(st.integers(min_value=3, max_value=8))
        pairs = []
        for _ in range(num_pairs):
            key = draw(st.one_of(string_strategy, st.just('"x"'), st.just('"y"'), st.just('"z"')))
            val = draw(st.one_of(
                st.just('12345678901234567890'),
                st.just('-12345678901234567890'),
                st.just('0.123456789'),
                st.just('-0.000001'),
                st.just('1e100'),
                st.just('-1e-100'),
                value_strategy()
            ))
            pairs.append(key + ':' + val)
        return '{' + ','.join(pairs) + '}'
    elif pattern == 2:
        # Deeply nested with varied key names
        depth = draw(st.integers(min_value=3, max_value=8))
        result = draw(value_strategy())
        for _ in range(depth):
            if draw(st.booleans()):
                result = '[' + result + ']'
            else:
                key = draw(st.one_of(string_strategy, st.just('"a"'), st.just('"b"'), st.just('"c"')))
                result = '{' + key + ':' + result + '}'
        return result
    elif pattern == 3:
        # Array with many strings of varying lengths
        num_vals = draw(st.integers(min_value=5, max_value=15))
        vals = []
        for _ in range(num_vals):
            # Generate strings with varied content
            parts = draw(st.lists(
                st.one_of(
                    st.text(alphabet=safe_string_chars, min_size=1, max_size=5),
                    st.sampled_from(['\\n', '\\t', '\\"', '\\\\']),
                    st.just(' '),
                    st.just('\x00')
                ),
                min_size=1, max_size=4
            ))
            vals.append('"' + ''.join(parts) + '"')
        return '[' + ','.join(vals) + ']'
    elif pattern == 4:
        # Object with arrays as values
        num_pairs = draw(st.integers(min_value=2, max_value=6))
        pairs = []
        for _ in range(num_pairs):
            key = draw(string_strategy)
            num_elems = draw(st.integers(min_value=1, max_value=5))
            elems = [draw(value_strategy()) for _ in range(num_elems)]
            pairs.append(key + ':[' + ','.join(elems) + ']')
        return '{' + ','.join(pairs) + '}'
    else:
        # Mixed structure with arrays of objects and objects of arrays
        if draw(st.booleans()):
            # Array of objects with arrays
            num_objs = draw(st.integers(min_value=2, max_value=5))
            objs = []
            for _ in range(num_objs):
                num_arrays = draw(st.integers(min_value=1, max_value=3))
                pairs = []
                for _ in range(num_arrays):
                    key = draw(st.one_of(string_strategy, st.just('"arr"')))
                    num_elems = draw(st.integers(min_value=1, max_value=4))
                    elems = [draw(value_strategy()) for _ in range(num_elems)]
                    pairs.append(key + ':[' + ','.join(elems) + ']')
                objs.append('{' + ','.join(pairs) + '}')
            return '[' + ','.join(objs) + ']'
        else:
            # Object with arrays of objects
            num_arrays = draw(st.integers(min_value=2, max_value=5))
            pairs = []
            for _ in range(num_arrays):
                key = draw(st.one_of(string_strategy, st.just('"objs"')))
                num_objs = draw(st.integers(min_value=1, max_value=3))
                objs = []
                for _ in range(num_objs):
                    num_pairs = draw(st.integers(min_value=1, max_value=3))
                    obj_pairs = []
                    for _ in range(num_pairs):
                        k = draw(string_strategy)
                        v = draw(value_strategy())
                        obj_pairs.append(k + ':' + v)
                    objs.append('{' + ','.join(obj_pairs) + '}')
                pairs.append(key + ':[' + ','.join(objs) + ']')
            return '{' + ','.join(pairs) + '}'

# Main strategy: combine all, bias toward objects with members
strategy = st.one_of(
    st.lists(obj_strategy(), min_size=1, max_size=5).map(lambda x: x[0]),
    st.lists(arr_strategy(), min_size=1, max_size=5).map(lambda x: x[0]),
    st.lists(value_strategy(), min_size=1, max_size=5).map(lambda x: x[0]),
    malformed,
    deep_nested(),
    generate_nesting_toward_cap(),
    diversified_structures(),
    broad_accepted_structures(),
    ultra_diverse(),
    st.just('{}'),
    st.just('[]'),
    st.just('{"a":1,"a":2}'),
    st.just('{"a":1,}'),
    st.just('[1,2,]'),
    st.just('1e309'),
    st.just('-'),
    st.just('"\\uD800\\uDC00"'),
    st.just('"\x00"'),
    st.just('"\x01\x1F"'),
    st.just('{"\x00":1}'),
    st.just('{"a":1,"b":2,"c":3}'),
    st.just('[[[[[{"a":1}]]]]]'),
)
```