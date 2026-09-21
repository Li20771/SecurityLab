// Ghidra headless postScript：反汇编 main() 前若干条指令，
// 用于证明 headless 自动分析不仅导入成功，而且真正完成了反汇编。
// 用法见 scripts/repro/verify_ghidra.sh。
// 源自 2026-09-20 审计脚本 tmp/audit/DumpDisasm.java（已版本化至此）。
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.Listing;

public class DumpDisasm extends GhidraScript {

    @Override
    public void run() throws Exception {
        println("GHIDRA_DISASM: language=" + currentProgram.getLanguageID());
        FunctionManager fm = currentProgram.getFunctionManager();

        Function main = null;
        for (Function f : fm.getFunctions(true)) {
            if ("main".equals(f.getName())) {
                main = f;
                break;
            }
        }

        if (main == null) {
            println("GHIDRA_DISASM: main NOT FOUND");
            return;
        }

        println("GHIDRA_DISASM: main entry=" + main.getEntryPoint());
        Listing listing = currentProgram.getListing();
        Instruction ins = listing.getInstructionAt(main.getEntryPoint());

        int n = 0;
        while (ins != null && n < 12) {
            println("GHIDRA_DISASM: " + ins.getAddress() + "  " + ins.toString());
            ins = ins.getNext();
            n++;
        }
        println("GHIDRA_DISASM: OK disassembled=" + n);
    }
}
