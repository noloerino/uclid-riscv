#!/usr/bin/env python3

"""
Generates a uclid module from a corresponding elf file form riscv-tests.
"""

import subprocess
from dataclasses import dataclass
from io import StringIO
import os
import re
import textwrap

OBJDUMP = "riscv64-unknown-elf-objdump"
# Exactly 8 hex digits, followed by a colon, then instruction hex, then the rest
TEXT_LINE_RE = re.compile("^([0-9a-f]{8}):\s+([0-9a-f]{8})\s+(.*)$")
# This output comes from objdump -s instead of objdump -D
DATA_LINE_RE = re.compile("([0-9a-f]{8})\s+" * 5)

def parse_elf(elf_path):
    inst_count = 0
    init_lines = []
    print("parsing file:", elf_path)
    text_p = subprocess.run([OBJDUMP, "-d", "-j", ".text.init", elf_path], stdout=subprocess.PIPE, check=True)
    curr_section = ""
    has_data = False
    reader = StringIO(text_p.stdout.decode("utf-8"))
    for line in reader.readlines():
        stripped = line.strip()
        if stripped.startswith("Disassembly of section "):
            # Last token is section name, which has a colon on the end
            curr_section = stripped.split()[-1][:-1]
        elif curr_section == ".text.init":
            m = re.match(TEXT_LINE_RE, stripped)
            if m:
                inst_pc = "0x" + "{:X}".format(int(m.group(1), 16) >> 2) + "bv30"
                inst_hex = "0x" + m.group(2).upper() + "bv32"
                inst_comment = m.group(3).strip().replace("\t", " ").replace(",", ", ")
                init_lines.append(f"assume (imem[{inst_pc}] == {inst_hex}); // {inst_comment}")
                inst_count += 1
            elif stripped:
                init_lines.append("// " + stripped)
        elif curr_section == ".data":
            has_data = True            
    init_lines.append(
        f"assume (forall (a : mem_word_addr_t) :: (a < IMEM_PC_START || a > IMEM_PC_START + {inst_count}bv30) ==> imem[a] == instructions.NOP);"
    )
    reader.close()
    # For some reason objdump -D doesn't show all data, so we need to do a second pass
    if has_data:
        data_p = subprocess.run([OBJDUMP, "-s", "-j", ".data", elf_path], stdout=subprocess.PIPE, check=True)
        started_data = False
        reader = StringIO(data_p.stdout.decode("utf-8"))
        for line in reader.readlines():
            stripped = line.strip()
            if stripped.startswith("Contents of section .data"):
                started_data = True
            elif started_data:
                m = re.match(DATA_LINE_RE, stripped)
                if m:
                    starting_addr_i = int(m.group(0), 16) >> 2
                    words = ["0x" + m.group(i).upper() + "bv32" for i in range(1, 5)]
                    for i, data in enumerate(words):
                        init_lines.append(f"assume (dmem[0x{'{:X}'.format(starting_addr_i + i)}bv30] == {data});")
        reader.close()
    init_block = '\n'.join(init_lines)
    s = textwrap.dedent(f"""\
        // AUTOGENERATED FROM {elf_path} BY {__file__}
        module main {{
            type * = common.*;
            define * = common.*;
            type * = instructions.*;
            const * = cpu.*;

            var imem : mem_t;

            instance cpu_0 : cpu (imem : (imem));

            init {{
{textwrap.indent(init_block, ' ' * 16)}
            }}

            next {{
                next (cpu_0);
                if (cpu_0.exception.cause != X_NONE) {{
                    // All tests are ended with an ECALL invocation
                    assert (cpu_0.exception.cause == X_ECALL);
                    assert (cpu_0.regfile[registers.a0] == 0bv32);
                }}
            }}

            // TODO run BMC to check LTL properties
            property[LTL] eventually_exits: G(F(cpu_0.exception.cause == X_ECALL));

            control {{
                vobj = bmc({inst_count});
                check;
                print_results;
                vobj.print_cex(cpu_0.pc, cpu_0.exception, cpu_0.regfile);
            }}
        }}""")
    os.makedirs("autogen-riscv-tests", exist_ok=True)
    with open(os.path.join("autogen-riscv-tests", os.path.basename(elf_path) + ".ucl"), "w") as f:
        f.write(s)

if __name__ == "__main__":
    for file in os.listdir("riscv-tests"):
        filename = os.fsdecode(file)
        if filename.startswith("rv32ui-p-") and "." not in filename:
            parse_elf(os.path.join("riscv-tests", filename))
