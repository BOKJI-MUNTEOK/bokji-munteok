import kr.dogfoot.hwplib.object.HWPFile;
import kr.dogfoot.hwplib.object.bodytext.ParagraphListInterface;
import kr.dogfoot.hwplib.object.bodytext.Section;
import kr.dogfoot.hwplib.object.bodytext.control.Control;
import kr.dogfoot.hwplib.object.bodytext.control.ControlTable;
import kr.dogfoot.hwplib.object.bodytext.control.table.Cell;
import kr.dogfoot.hwplib.object.bodytext.control.table.Row;
import kr.dogfoot.hwplib.object.bodytext.paragraph.Paragraph;
import kr.dogfoot.hwplib.object.bodytext.paragraph.lineseg.LineSegItem;
import kr.dogfoot.hwplib.object.bodytext.paragraph.text.HWPChar;
import kr.dogfoot.hwplib.object.bodytext.paragraph.text.HWPCharControlChar;
import kr.dogfoot.hwplib.object.bodytext.paragraph.text.HWPCharNormal;
import kr.dogfoot.hwplib.object.bodytext.paragraph.text.HWPCharType;
import kr.dogfoot.hwplib.object.bodytext.paragraph.text.ParaText;
import kr.dogfoot.hwplib.reader.HWPReader;
import kr.dogfoot.hwplib.writer.HWPWriter;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class HwpLibTool {
    public static void main(String[] args) throws Exception {
        if (args.length < 2) {
            System.err.println("Usage: inspect <in.hwp> | fill <in.hwp> <plan.json> <out.hwp>");
            System.exit(2);
        }
        String cmd = args[0];
        if ("inspect".equals(cmd)) {
            HWPFile hwp = HWPReader.fromFile(args[1]);
            System.out.println(inspectToJson(hwp));
        } else if ("fill".equals(cmd)) {
            if (args.length < 4) {
                System.err.println("Usage: fill <in.hwp> <plan.json> <out.hwp>");
                System.exit(2);
            }
            HWPFile hwp = HWPReader.fromFile(args[1]);
            String jsonSrc = Files.readString(Path.of(args[2]), StandardCharsets.UTF_8);
            @SuppressWarnings("unchecked")
            Map<String, Object> plan = (Map<String, Object>) new MiniJsonParser(jsonSrc).parseValue();
            int applied = applyPlan(hwp, plan);
            HWPWriter.toFile(hwp, args[3]);
            System.out.println("FILL_OK applied=" + applied);
        } else {
            System.err.println("Unknown command: " + cmd);
            System.exit(2);
        }
    }

    private static String safeParaString(Paragraph p) {
        if (p.getText() == null) {
            return "";
        }
        try {
            String s = p.getNormalString();
            if (s == null) return "";
            return s.replace("\r", "").replace("\n", "");
        } catch (Exception e) {
            return "";
        }
    }

    private static String cellJoinedText(Cell cell) {
        var plist = cell.getParagraphList();
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < plist.getParagraphCount(); i++) {
            if (i > 0) sb.append('\n');
            sb.append(safeParaString(plist.getParagraph(i)));
        }
        return sb.toString();
    }

    private static String jsonEscape(String s) {
        StringBuilder sb = new StringBuilder(s.length() + 16);
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char ch = s.charAt(i);
            switch (ch) {
                case '"' -> sb.append("\\\"");
                case '\\' -> sb.append("\\\\");
                case '\b' -> sb.append("\\b");
                case '\f' -> sb.append("\\f");
                case '\n' -> sb.append("\\n");
                case '\r' -> sb.append("\\r");
                case '\t' -> sb.append("\\t");
                default -> {
                    if (ch < 0x20) {
                        sb.append(String.format("\\u%04x", (int) ch));
                    } else {
                        sb.append(ch);
                    }
                }
            }
        }
        sb.append('"');
        return sb.toString();
    }

    private static String inspectToJson(HWPFile hwp) {
        StringBuilder sb = new StringBuilder(4096);
        sb.append("{\"paragraphs\":[");
        boolean firstPara = true;
        List<Map<String, Object>> tableEntries = new ArrayList<>();

        var sections = hwp.getBodyText().getSectionList();
        for (int sIdx = 0; sIdx < sections.size(); sIdx++) {
            Section sec = sections.get(sIdx);
            for (int pIdx = 0; pIdx < sec.getParagraphCount(); pIdx++) {
                Paragraph p = sec.getParagraph(pIdx);
                if (!firstPara) sb.append(',');
                firstPara = false;
                sb.append("{\"section\":").append(sIdx)
                  .append(",\"para\":").append(pIdx)
                  .append(",\"text\":").append(jsonEscape(safeParaString(p))).append('}');

                if (p.getControlList() != null) {
                    var ctrls = p.getControlList();
                    for (int cIdx = 0; cIdx < ctrls.size(); cIdx++) {
                        Control c = ctrls.get(cIdx);
                        if (c instanceof ControlTable ct) {
                            Map<String, Object> tm = new LinkedHashMap<>();
                            tm.put("section", sIdx);
                            tm.put("para", pIdx);
                            tm.put("control", cIdx);
                            tm.put("table", ct);
                            tableEntries.add(tm);
                        }
                    }
                }
            }
        }
        sb.append("],\"tables\":[");
        for (int tIdx = 0; tIdx < tableEntries.size(); tIdx++) {
            if (tIdx > 0) sb.append(',');
            Map<String, Object> tm = tableEntries.get(tIdx);
            ControlTable ct = (ControlTable) tm.get("table");
            int rowCount = ct.getRowList().size();
            int colCount = ct.getTable().getColumnCount();
            sb.append("{\"section\":").append(tm.get("section"))
              .append(",\"para\":").append(tm.get("para"))
              .append(",\"control\":").append(tm.get("control"))
              .append(",\"row_count\":").append(rowCount)
              .append(",\"col_count\":").append(colCount)
              .append(",\"cells\":[");
            boolean firstCell = true;
            int cellSeq = 0;
            for (Row r : ct.getRowList()) {
                for (Cell cell : r.getCellList()) {
                    var lh = cell.getListHeader();
                    if (!firstCell) sb.append(',');
                    firstCell = false;
                    sb.append("{\"cell_idx\":").append(cellSeq++)
                      .append(",\"row\":").append(lh.getRowIndex())
                      .append(",\"col\":").append(lh.getColIndex())
                      .append(",\"row_span\":").append(lh.getRowSpan())
                      .append(",\"col_span\":").append(lh.getColSpan())
                      .append(",\"text\":").append(jsonEscape(cellJoinedText(cell))).append('}');
                }
            }
            sb.append("]}");
        }
        sb.append("]}");
        return sb.toString();
    }

    @SuppressWarnings("unchecked")
    private static int applyPlan(HWPFile hwp, Map<String, Object> plan) throws Exception {
        List<ControlTable> tables = collectTables(hwp);
        int applied = 0;

        Object ceObj = plan.get("cell_edits");
        if (ceObj instanceof List<?> ceList) {
            for (Object item : ceList) {
                Map<String, Object> ed = (Map<String, Object>) item;
                int tIdx = asInt(ed.get("table_index"));
                int row = asInt(ed.get("row"));
                int col = asInt(ed.get("col"));
                int cpIdx = asInt(ed.get("cell_para_idx"));
                String mode = asStr(ed.get("mode"));
                String anchor = asStr(ed.get("anchor"));
                String value = asStr(ed.get("value"));
                if (value.isEmpty()) value = asStr(ed.get("new_text"));

                if (tIdx < 0 || tIdx >= tables.size()) continue;
                Cell cell = findCell(tables.get(tIdx), row, col);
                if (cell == null) continue;
                ParagraphListInterface plist = cell.getParagraphList();
                if ("add_para".equals(mode)) {
                    if (addParagraphToList(plist, value)) applied++;
                    continue;
                }
                if (cpIdx < 0 || cpIdx >= plist.getParagraphCount()) continue;
                if (applyParaEdit(plist.getParagraph(cpIdx), mode, anchor, value)) {
                    applied++;
                }
            }
        }

        Object peObj = plan.get("para_edits");
        if (peObj instanceof List<?> peList) {
            for (Object item : peList) {
                Map<String, Object> ed = (Map<String, Object>) item;
                int secIdx = asInt(ed.get("section"));
                int pIdx = asInt(ed.get("para"));
                String mode = asStr(ed.get("mode"));
                String anchor = asStr(ed.get("anchor"));
                String value = asStr(ed.get("value"));
                if (value.isEmpty()) value = asStr(ed.get("new_text"));

                var secs = hwp.getBodyText().getSectionList();
                if (secIdx < 0 || secIdx >= secs.size()) continue;
                Section sec = secs.get(secIdx);
                if (pIdx < 0 || pIdx >= sec.getParagraphCount()) continue;
                if (applyParaEdit(sec.getParagraph(pIdx), mode, anchor, value)) {
                    applied++;
                }
            }
        }
        return applied;
    }

    private static List<ControlTable> collectTables(HWPFile hwp) {
        List<ControlTable> out = new ArrayList<>();
        for (Section sec : hwp.getBodyText().getSectionList()) {
            for (int i = 0; i < sec.getParagraphCount(); i++) {
                Paragraph p = sec.getParagraph(i);
                if (p.getControlList() == null) continue;
                for (Control c : p.getControlList()) {
                    if (c instanceof ControlTable ct) {
                        out.add(ct);
                    }
                }
            }
        }
        return out;
    }

    private static Cell findCell(ControlTable ct, int row, int col) {
        for (Row r : ct.getRowList()) {
            for (Cell cell : r.getCellList()) {
                var lh = cell.getListHeader();
                if (lh.getRowIndex() == row && lh.getColIndex() == col) {
                    return cell;
                }
            }
        }
        return null;
    }

    private static boolean addParagraphToList(ParagraphListInterface plist, String value) throws Exception {
        int cnt = plist.getParagraphCount();
        if (cnt <= 0 || value == null || value.isEmpty()) return false;
        Paragraph prev = plist.getParagraph(cnt - 1);
        prev.getHeader().setLastInList(false);

        Paragraph np = plist.addNewParagraph();
        np.getHeader().setLastInList(true);
        np.getHeader().setParaShapeId(prev.getHeader().getParaShapeId());
        np.getHeader().setStyleId(prev.getHeader().getStyleId());
        np.getHeader().setCharShapeCount(1);
        np.getHeader().setLineAlignCount(1);

        long shapeId = 0;
        if (prev.getCharShape() != null && !prev.getCharShape().getPositonShapeIdPairList().isEmpty()) {
            shapeId = prev.getCharShape().getPositonShapeIdPairList().get(0).getShapeId();
        }
        np.createCharShape();
        np.getCharShape().addParaCharShape(0, shapeId);

        np.createText();
        np.getText().addString(" " + value);
        np.getHeader().setCharacterCount(np.getText().getCharSize());

        if (prev.getLineSeg() != null && !prev.getLineSeg().getLineSegItemList().isEmpty()) {
            var prevLs = prev.getLineSeg().getLineSegItemList().get(prev.getLineSeg().getLineSegItemList().size() - 1);
            np.createLineSeg();
            LineSegItem item = prevLs.clone();
            item.setTextStartPosition(0);
            item.setLineVerticalPosition(
                prevLs.getLineVerticalPosition() + prevLs.getLineHeight() + Math.max(400, prevLs.getLineSpace())
            );
            np.getLineSeg().getLineSegItemList().add(item);
        }
        return true;
    }

    private static int findNormalEndIndex(ParaText pt) {
        ArrayList<HWPChar> chars = pt.getCharList();
        for (int i = chars.size() - 1; i >= 0; i--) {
            if (chars.get(i).getType() == HWPCharType.ControlChar) {
                HWPCharControlChar cc = (HWPCharControlChar) chars.get(i);
                if (cc.getCode() == 13) {
                    return i;
                }
            }
        }
        return chars.size();
    }

    private static int findFirstInsertIndex(ParaText pt) {
        ArrayList<HWPChar> chars = pt.getCharList();
        for (int i = 0; i < chars.size(); i++) {
            if (chars.get(i).getType() == HWPCharType.Normal) {
                return i;
            }
        }
        return findNormalEndIndex(pt);
    }

    private static void trimFollowingAsciiSpaces(ParaText pt, int startIdx, int maxTrim, int minKeep) {
        ArrayList<HWPChar> chars = pt.getCharList();
        int count = 0;
        for (int i = startIdx; i < chars.size(); i++) {
            HWPChar ch = chars.get(i);
            if (ch.getType() == HWPCharType.Normal && ((HWPCharNormal) ch).getCode() == 32) {
                count++;
            } else {
                break;
            }
        }
        int toRemove = Math.min(Math.max(0, count - minKeep), maxTrim);
        for (int k = 0; k < toRemove; k++) {
            chars.remove(startIdx);
        }
    }

    private static int findCharIndexOfSubstringEnd(ParaText pt, String sub) {
        StringBuilder sb = new StringBuilder();
        List<Integer> map = new ArrayList<>();
        ArrayList<HWPChar> chars = pt.getCharList();
        for (int i = 0; i < chars.size(); i++) {
            HWPChar ch = chars.get(i);
            if (ch.getType() == HWPCharType.Normal) {
                sb.append((char) ((HWPCharNormal) ch).getCode());
                map.add(i);
            }
        }
        int pos = sb.toString().indexOf(sub);
        if (pos < 0) return -1;
        int endStrIdx = pos + sub.length();
        if (endStrIdx == 0) return map.isEmpty() ? 0 : map.get(0);
        if (endStrIdx <= map.size()) {
            return map.get(endStrIdx - 1) + 1;
        }
        return findNormalEndIndex(pt);
    }

    private static int findCharIndexOfSubstringStart(ParaText pt, String sub) {
        StringBuilder sb = new StringBuilder();
        List<Integer> map = new ArrayList<>();
        ArrayList<HWPChar> chars = pt.getCharList();
        for (int i = 0; i < chars.size(); i++) {
            HWPChar ch = chars.get(i);
            if (ch.getType() == HWPCharType.Normal) {
                sb.append((char) ((HWPCharNormal) ch).getCode());
                map.add(i);
            }
        }
        int pos = sb.toString().indexOf(sub);
        if (pos < 0 || pos >= map.size()) return -1;
        return map.get(pos);
    }

    private static boolean applyParaEdit(Paragraph p, String mode, String anchor, String value) throws Exception {
        if (p.getText() == null) {
            p.createText();
        }
        ParaText pt = p.getText();
        boolean ok = false;
        if ("set".equals(mode)) {
            if (pt.getCharList().isEmpty()) {
                pt.addString(value);
            } else {
                pt.insertString(findNormalEndIndex(pt), value);
            }
            ok = true;
        } else if ("append".equals(mode)) {
            pt.insertString(findNormalEndIndex(pt), " " + value);
            ok = true;
        } else if ("prepend_trim_spaces".equals(mode)) {
            int idx = findFirstInsertIndex(pt);
            boolean hasLeadingSpaces = idx < pt.getCharList().size()
                && pt.getCharList().get(idx).getType() == HWPCharType.Normal
                && ((HWPCharNormal) pt.getCharList().get(idx)).getCode() == 32;
            String ins = hasLeadingSpaces ? value : (value + "   ");
            pt.insertString(idx, ins);
            if (hasLeadingSpaces) {
                trimFollowingAsciiSpaces(pt, idx + ins.length(), value.length(), 3);
            }
            ok = true;
        } else if ("after_anchor".equals(mode) && !anchor.isEmpty()) {
            int idx = findCharIndexOfSubstringEnd(pt, anchor);
            if (idx >= 0) {
                String ins = " " + value;
                pt.insertString(idx, ins);
                trimFollowingAsciiSpaces(pt, idx + ins.length(), value.length() + 1, 2);
                ok = true;
            }
        } else if ("date".equals(mode)) {
            Matcher m = Pattern.compile("(\\d{4})년\\s*(\\d{1,2})월\\s*(\\d{1,2})일").matcher(value);
            if (m.find()) {
                String yr = m.group(1), mo = m.group(2), dy = m.group(3);
                int idxM = findCharIndexOfSubstringEnd(pt, "월");
                int idxYEnd = findCharIndexOfSubstringEnd(pt, "년");
                int idxYStart = findCharIndexOfSubstringStart(pt, "년");
                if (idxM >= 0 && idxYEnd >= 0 && idxYStart >= 0) {
                    pt.insertString(idxM, " " + dy);
                    trimFollowingAsciiSpaces(pt, idxM + 1 + dy.length(), dy.length() + 1, 2);
                    pt.insertString(idxYEnd, " " + mo);
                    trimFollowingAsciiSpaces(pt, idxYEnd + 1 + mo.length(), mo.length() + 1, 2);
                    pt.insertString(idxYStart, yr);
                    ok = true;
                }
            }
        } else if ("date20".equals(mode)) {
            Matcher m = Pattern.compile("(\\d{4})년\\s*(\\d{1,2})월\\s*(\\d{1,2})일").matcher(value);
            if (m.find()) {
                String yr2 = m.group(1).substring(2), mo = m.group(2), dy = m.group(3);
                int idxM = findCharIndexOfSubstringEnd(pt, "월");
                int idxYEnd = findCharIndexOfSubstringEnd(pt, "년");
                int idx20End = findCharIndexOfSubstringEnd(pt, "20");
                if (idxM >= 0 && idxYEnd >= 0 && idx20End >= 0) {
                    pt.insertString(idxM, " " + dy);
                    trimFollowingAsciiSpaces(pt, idxM + 1 + dy.length(), dy.length() + 1, 1);
                    pt.insertString(idxYEnd, " " + mo);
                    trimFollowingAsciiSpaces(pt, idxYEnd + 1 + mo.length(), mo.length() + 1, 1);
                    pt.insertString(idx20End, yr2);
                    trimFollowingAsciiSpaces(pt, idx20End + yr2.length(), yr2.length(), 1);
                    ok = true;
                }
            }
        }
        if (ok) {
            p.getHeader().setCharacterCount(pt.getCharSize());
        }
        return ok;
    }

    private static int asInt(Object o) {
        return o instanceof Number n ? n.intValue() : 0;
    }

    private static String asStr(Object o) {
        return o == null ? "" : String.valueOf(o);
    }

    private static final class MiniJsonParser {
        private final String src;
        private int pos = 0;

        MiniJsonParser(String src) {
            this.src = src;
        }

        Object parseValue() {
            skipWs();
            if (pos >= src.length()) throw new IllegalArgumentException("Unexpected EOF");
            char c = src.charAt(pos);
            return switch (c) {
                case '{' -> parseObject();
                case '[' -> parseArray();
                case '"' -> parseString();
                case 't', 'f' -> parseBoolean();
                case 'n' -> parseNull();
                default -> parseNumber();
            };
        }

        private Map<String, Object> parseObject() {
            pos++;
            Map<String, Object> map = new LinkedHashMap<>();
            skipWs();
            if (pos < src.length() && src.charAt(pos) == '}') {
                pos++;
                return map;
            }
            while (true) {
                skipWs();
                String key = parseString();
                skipWs();
                if (src.charAt(pos++) != ':') throw new IllegalArgumentException("Expected ':'");
                Object val = parseValue();
                map.put(key, val);
                skipWs();
                char ch = src.charAt(pos++);
                if (ch == '}') break;
                if (ch != ',') throw new IllegalArgumentException("Expected ',' or '}'");
            }
            return map;
        }

        private List<Object> parseArray() {
            pos++;
            List<Object> list = new ArrayList<>();
            skipWs();
            if (pos < src.length() && src.charAt(pos) == ']') {
                pos++;
                return list;
            }
            while (true) {
                list.add(parseValue());
                skipWs();
                char ch = src.charAt(pos++);
                if (ch == ']') break;
                if (ch != ',') throw new IllegalArgumentException("Expected ',' or ']'");
            }
            return list;
        }

        private String parseString() {
            if (src.charAt(pos++) != '"') throw new IllegalArgumentException("Expected '\"'");
            StringBuilder sb = new StringBuilder();
            while (pos < src.length()) {
                char c = src.charAt(pos++);
                if (c == '"') return sb.toString();
                if (c == '\\') {
                    char esc = src.charAt(pos++);
                    switch (esc) {
                        case '"', '\\', '/' -> sb.append(esc);
                        case 'b' -> sb.append('\b');
                        case 'f' -> sb.append('\f');
                        case 'n' -> sb.append('\n');
                        case 'r' -> sb.append('\r');
                        case 't' -> sb.append('\t');
                        case 'u' -> {
                            int cp = Integer.parseInt(src.substring(pos, pos + 4), 16);
                            sb.append((char) cp);
                            pos += 4;
                        }
                        default -> sb.append(esc);
                    }
                } else {
                    sb.append(c);
                }
            }
            throw new IllegalArgumentException("Unterminated string");
        }

        private Boolean parseBoolean() {
            if (src.startsWith("true", pos)) {
                pos += 4;
                return Boolean.TRUE;
            }
            if (src.startsWith("false", pos)) {
                pos += 5;
                return Boolean.FALSE;
            }
            throw new IllegalArgumentException("Invalid boolean");
        }

        private Object parseNull() {
            if (src.startsWith("null", pos)) {
                pos += 4;
                return null;
            }
            throw new IllegalArgumentException("Invalid null");
        }

        private Number parseNumber() {
            int start = pos;
            if (src.charAt(pos) == '-') pos++;
            while (pos < src.length() && Character.isDigit(src.charAt(pos))) pos++;
            boolean isFloat = false;
            if (pos < src.length() && src.charAt(pos) == '.') {
                isFloat = true;
                pos++;
                while (pos < src.length() && Character.isDigit(src.charAt(pos))) pos++;
            }
            if (pos < src.length() && (src.charAt(pos) == 'e' || src.charAt(pos) == 'E')) {
                isFloat = true;
                pos++;
                if (pos < src.length() && (src.charAt(pos) == '+' || src.charAt(pos) == '-')) pos++;
                while (pos < src.length() && Character.isDigit(src.charAt(pos))) pos++;
            }
            String num = src.substring(start, pos);
            return isFloat ? Double.parseDouble(num) : Long.parseLong(num);
        }

        private void skipWs() {
            while (pos < src.length() && Character.isWhitespace(src.charAt(pos))) pos++;
        }
    }
}
