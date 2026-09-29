"""
Pure Python, zero-dependency QR Code generator for ITMS Verification Copilot.
Generates scannable Unicode terminal QR codes for the Textual TUI and consoles.
"""
from typing import List, Tuple

# Mode constants
MODE_8BIT_BYTE = 4

# Error correction levels (M is standard 15% recovery)
EC_L = 1
EC_M = 0
EC_Q = 3
EC_H = 2

# Generator polynomials
def _gexp(n: int) -> int:
    while n < 0:
        n += 255
    while n >= 256:
        n -= 255
    return EXP_TABLE[n]

def _glog(n: int) -> int:
    if n < 1:
        raise ValueError(f"glog({n})")
    return LOG_TABLE[n]

EXP_TABLE = [0] * 256
LOG_TABLE = [0] * 256

# Initialize Galois field tables GF(256)
for i in range(8):
    EXP_TABLE[i] = 1 << i
for i in range(8, 256):
    EXP_TABLE[i] = EXP_TABLE[i - 4] ^ EXP_TABLE[i - 5] ^ EXP_TABLE[i - 6] ^ EXP_TABLE[i - 8]
for i in range(255):
    LOG_TABLE[EXP_TABLE[i]] = i


class Polynomial:
    def __init__(self, num: List[int], shift: int = 0):
        offset = 0
        while offset < len(num) and num[offset] == 0:
            offset += 1
        self.num = num[offset:] + [0] * shift

    def get(self, index: int) -> int:
        return self.num[index]

    def __len__(self) -> int:
        return len(self.num)

    def multiply(self, e: "Polynomial") -> "Polynomial":
        num = [0] * (len(self) + len(e) - 1)
        for i in range(len(self)):
            for j in range(len(e)):
                num[i + j] ^= _gexp(_glog(self.get(i)) + _glog(e.get(j)))
        return Polynomial(num, 0)

    def mod(self, e: "Polynomial") -> "Polynomial":
        if len(self) - len(e) < 0:
            return self
        ratio = _glog(self.get(0)) - _glog(e.get(0))
        num = list(self.num)
        for i in range(len(e)):
            num[i] ^= _gexp(_glog(e.get(i)) + ratio)
        return Polynomial(num, 0).mod(e)


RS_BLOCK_TABLE = [
    # Ver 1
    [1, 26, 19], [1, 26, 16], [1, 26, 13], [1, 26, 9],
    # Ver 2
    [1, 44, 34], [1, 44, 28], [1, 44, 22], [1, 44, 16],
    # Ver 3
    [1, 70, 55], [1, 70, 44], [2, 35, 17], [2, 35, 13],
    # Ver 4
    [1, 100, 80], [2, 50, 32], [2, 50, 24], [4, 25, 9],
    # Ver 5
    [1, 134, 108], [2, 67, 43], [2, 33, 15, 2, 34, 16], [2, 33, 11, 2, 34, 12],
]

ALIGNMENT_PATTERN_POSITIONS = [
    [],
    [6, 18],
    [6, 22],
    [6, 26],
    [6, 30],
]


class BitBuffer:
    def __init__(self):
        self.buffer = []
        self.length = 0

    def put(self, num: int, length: int):
        for i in range(length):
            self.put_bit(((num >> (length - i - 1)) & 1) == 1)

    def put_bit(self, bit: bool):
        buf_index = self.length // 8
        if len(self.buffer) <= buf_index:
            self.buffer.append(0)
        if bit:
            self.buffer[buf_index] |= (0x80 >> (self.length % 8))
        self.length += 1


def _get_rs_blocks(type_num: int, ec_level: int) -> List[Tuple[int, int]]:
    idx = (type_num - 1) * 4 + ec_level
    if idx < len(RS_BLOCK_TABLE):
        entry = RS_BLOCK_TABLE[idx]
        blocks = []
        # format: count, total, data
        i = 0
        while i < len(entry):
            count, total, data = entry[i], entry[i + 1], entry[i + 2]
            for _ in range(count):
                blocks.append((total, data))
            i += 3
        return blocks
    return [(100, 80)]


def _get_error_correct_poly(error_length: int) -> Polynomial:
    a = Polynomial([1], 0)
    for i in range(error_length):
        a = a.multiply(Polynomial([1, _gexp(i)], 0))
    return a


class QRCode:
    def __init__(self, type_num: int = 3, ec_level: int = EC_M):
        self.type_num = type_num
        self.ec_level = ec_level
        self.module_count = type_num * 4 + 17
        self.modules: List[List[bool]] = []

    def make_matrix(self, data_str: str) -> List[List[bool]]:
        raw_bytes = data_str.encode("utf-8")
        # Auto-size version if needed
        for ver in range(1, 6):
            blocks = _get_rs_blocks(ver, self.ec_level)
            total_cap = sum(data for _, data in blocks)
            # Overhead: 4 bits mode + 8 bits length
            if len(raw_bytes) + 2 <= total_cap:
                self.type_num = ver
                break

        self.module_count = self.type_num * 4 + 17
        self.modules = [[None] * self.module_count for _ in range(self.module_count)]

        # 1. Patterns
        self._setup_position_probe(0, 0)
        self._setup_position_probe(self.module_count - 7, 0)
        self._setup_position_probe(0, self.module_count - 7)
        self._setup_timing_pattern()
        self._setup_alignment_pattern()

        # 2. Data encoding
        data = self._create_data(raw_bytes)

        # 3. Format info (Mask 0)
        self._setup_format_info(mask=0)

        # 4. Map data with mask 0
        self._map_data(data, mask=0)

        # Fill any remaining None with False
        for r in range(self.module_count):
            for c in range(self.module_count):
                if self.modules[r][c] is None:
                    self.modules[r][c] = False

        return self.modules

    def _setup_position_probe(self, row: int, col: int):
        for r in range(-1, 8):
            if row + r <= -1 or self.module_count <= row + r:
                continue
            for c in range(-1, 8):
                if col + c <= -1 or self.module_count <= col + c:
                    continue
                if (0 <= r <= 6 and (c == 0 or c == 6)) or (0 <= c <= 6 and (r == 0 or r == 6)) or (2 <= r <= 4 and 2 <= c <= 4):
                    self.modules[row + r][col + c] = True
                else:
                    self.modules[row + r][col + c] = False

    def _setup_timing_pattern(self):
        for r in range(8, self.module_count - 8):
            if self.modules[r][6] is None:
                self.modules[r][6] = (r % 2 == 0)
        for c in range(8, self.module_count - 8):
            if self.modules[6][c] is None:
                self.modules[6][c] = (c % 2 == 0)

    def _setup_alignment_pattern(self):
        pos = ALIGNMENT_PATTERN_POSITIONS[self.type_num - 1] if self.type_num <= len(ALIGNMENT_PATTERN_POSITIONS) else []
        for r_pos in pos:
            for c_pos in pos:
                if self.modules[r_pos][c_pos] is not None:
                    continue
                for r in range(-2, 3):
                    for c in range(-2, 3):
                        if r == -2 or r == 2 or c == -2 or c == 2 or (r == 0 and c == 0):
                            self.modules[r_pos + r][c_pos + c] = True
                        else:
                            self.modules[r_pos + r][c_pos + c] = False

    def _setup_format_info(self, mask: int = 0):
        # Format info bits for EC_M (00) and mask 0 (000) -> 0x5412 with BCH
        # Constant for EC_M, mask 0
        bits = 0b101010000010010
        for i in range(15):
            mod = ((bits >> i) & 1) == 1
            if i < 6:
                self.modules[i][8] = mod
            elif i < 8:
                self.modules[i + 1][8] = mod
            else:
                self.modules[self.module_count - 15 + i][8] = mod

            if i < 8:
                self.modules[8][self.module_count - i - 1] = mod
            elif i < 9:
                self.modules[8][15 - i - 1 + 1] = mod
            else:
                self.modules[8][15 - i - 1] = mod

        self.modules[self.module_count - 8][8] = True

    def _create_data(self, raw_bytes: bytes) -> List[int]:
        blocks = _get_rs_blocks(self.type_num, self.ec_level)
        total_data = sum(data for _, data in blocks)
        total_code = sum(total for total, _ in blocks)

        buf = BitBuffer()
        buf.put(MODE_8BIT_BYTE, 4)
        buf.put(len(raw_bytes), 8)
        for b in raw_bytes:
            buf.put(b, 8)

        # Padding
        if buf.length + 4 <= total_data * 8:
            buf.put(0, 4)
        while buf.length % 8 != 0:
            buf.put_bit(False)
        while buf.length < total_data * 8:
            buf.put(0xEC, 8)
            if buf.length < total_data * 8:
                buf.put(0x11, 8)

        # RS Code Generation
        offset = 0
        dc_data = []
        ec_data = []
        for total_count, dc_count in blocks:
            ec_count = total_count - dc_count
            chunk = [0xFF & buf.buffer[i + offset] for i in range(dc_count)]
            offset += dc_count
            dc_data.append(chunk)

            rs_poly = _get_error_correct_poly(ec_count)
            raw_poly = Polynomial(chunk, rs_poly.num.__len__() - 1)
            mod_poly = raw_poly.mod(rs_poly)
            ec_chunk = [0] * ec_count
            for i in range(len(ec_chunk)):
                mod_index = i + len(mod_poly) - len(ec_chunk)
                ec_chunk[i] = mod_poly.get(mod_index) if mod_index >= 0 else 0
            ec_data.append(ec_chunk)

        # Interleave
        result = []
        max_dc = max(len(c) for c in dc_data)
        for i in range(max_dc):
            for c in dc_data:
                if i < len(c):
                    result.append(c[i])

        max_ec = max(len(c) for c in ec_data)
        for i in range(max_ec):
            for c in ec_data:
                if i < len(c):
                    result.append(c[i])

        return result

    def _map_data(self, data: List[int], mask: int = 0):
        inc = -1
        row = self.module_count - 1
        bit_index = 7
        byte_index = 0

        col = self.module_count - 1
        while col > 0:
            if col == 6:
                col -= 1
            while True:
                for c in range(2):
                    if self.modules[row][col - c] is None:
                        dark = False
                        if byte_index < len(data):
                            dark = (((data[byte_index] >> bit_index) & 1) == 1)
                        # mask 0: (row + col) % 2 == 0
                        if (row + (col - c)) % 2 == 0:
                            dark = not dark
                        self.modules[row][col - c] = dark
                        bit_index -= 1
                        if bit_index == -1:
                            byte_index += 1
                            bit_index = 7
                row += inc
                if row < 0 or self.module_count <= row:
                    row -= inc
                    inc = -inc
                    break
            col -= 2


def generate_qr_matrix(text: str) -> List[List[bool]]:
    """Generates a 2D boolean matrix of QR modules using standard qrcode library."""
    try:
        import qrcode
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=1,
            border=2,
        )
        qr.add_data(text)
        qr.make(fit=True)
        return qr.get_matrix()
    except Exception:
        qr = QRCode(type_num=3, ec_level=EC_M)
        return qr.make_matrix(text)


def generate_terminal_qr(text: str, quiet_zone: int = 2) -> str:
    """
    Renders an ASCII QR code string using half-block characters
    (▀, ▄, █, and space).
    """
    try:
        import qrcode
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=1,
            border=quiet_zone,
        )
        qr.add_data(text)
        qr.make(fit=True)
        padded = qr.get_matrix()
    except Exception:
        matrix = generate_qr_matrix(text)
        size = len(matrix)
        padded_size = size + quiet_zone * 2
        padded = [[False] * padded_size for _ in range(padded_size)]
        for r in range(size):
            for c in range(size):
                padded[r + quiet_zone][c + quiet_zone] = matrix[r][c]

    padded_size = len(padded)
    lines = []
    for r in range(0, padded_size, 2):
        row_top = padded[r]
        row_bot = padded[r + 1] if r + 1 < padded_size else [False] * padded_size

        line_chars = []
        for c in range(padded_size):
            top_dark = row_top[c]
            bot_dark = row_bot[c]

            if top_dark and bot_dark:
                line_chars.append("█")
            elif top_dark and not bot_dark:
                line_chars.append("▀")
            elif not top_dark and bot_dark:
                line_chars.append("▄")
            else:
                line_chars.append(" ")
        lines.append("".join(line_chars))

    return "\n".join(lines)


def generate_rich_qr(text: str, quiet_zone: int = 2):
    """
    Renders a compact, scannable Rich Text QR code for Textual TUI using Unicode
    half-block characters ('▀', '▄', '█', ' ') with high-contrast 'black on white' styling.
    Each character cell represents two vertical QR modules, making the QR code
    50% shorter and 50% narrower so it fits cleanly on terminal dashboards.
    """
    from rich.text import Text

    try:
        import qrcode
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=1,
            border=quiet_zone,
        )
        qr.add_data(text)
        qr.make(fit=True)
        padded = qr.get_matrix()
    except Exception:
        matrix = generate_qr_matrix(text)
        size = len(matrix)
        padded_size = size + quiet_zone * 2
        padded = [[False] * padded_size for _ in range(padded_size)]
        for r in range(size):
            for c in range(size):
                padded[r + quiet_zone][c + quiet_zone] = matrix[r][c]

    padded_size = len(padded)
    res = Text(no_wrap=True)
    for r in range(0, padded_size, 2):
        row_top = padded[r]
        row_bot = padded[r + 1] if r + 1 < padded_size else [False] * padded_size

        line_chars = []
        for c in range(padded_size):
            top_dark = row_top[c]
            bot_dark = row_bot[c]

            if top_dark and bot_dark:
                line_chars.append("█")
            elif top_dark and not bot_dark:
                line_chars.append("▀")
            elif not top_dark and bot_dark:
                line_chars.append("▄")
            else:
                line_chars.append(" ")

        res.append("".join(line_chars), style="black on white")
        res.append("\n")
    return res
