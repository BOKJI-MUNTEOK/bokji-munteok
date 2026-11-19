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
import kr.dogfoot.hwplib.object.docinfo.CharShape;
import kr.dogfoot.hwplib.object.docinfo.ParaShape;
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
            System.err.println("Usage: inspect <in.hwp> | fill <in.hwp> <plan.json|-> <out.hwp>");
            System.exit(2);
        }
        String cmd = args[0];
        if ("inspect".equals(cmd)) {
            HWPFile hwp = HWPReader.fromFile(args[1]);
            System.out.println(inspectToJson(hwp));
        } else if ("fill".equals(cmd)) {
            if (args.length < 4) {
                System.err.println("Usage: fill <in.hwp> <plan.json|-> <out.hwp>");
                System.exit(2);
            }
            HWPFile hwp = HWPReader.fromFile(args[1]);
            String jsonSrc = "-".equals(args[2])
                    ? new String(System.in.readAllBytes(), StandardCharsets.UTF_8)
                    : Files.readString(Path.of(args[2]), StandardCharsets.UTF_8);
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
            ArrayList<HWPChar> chars = p.getText().getCharList();
            StringBuilder sb = new StringBuilder(chars.size());
            for (HWPChar ch : chars) {
                if (ch.getType() == HWPCharType.Normal) {
                    int code = ((HWPCharNormal) ch).getCode();
                    if (code > 0 && code != '\r' && code != '\n') {
                        sb.append((char) code);
                    }
                } else if (ch.getType() == HWPCharType.ControlChar) {
                    if (((HWPCharControlChar) ch).getCode() == 10) {
                        sb.append('\u2028');
                    }
                }
            }
            return sb.toString();
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

    private static int paraCharShapeId(Paragraph p) {
        if (p != null && p.getCharShape() != null && !p.getCharShape().getPositonShapeIdPairList().isEmpty()) {
            return (int) p.getCharShape().getPositonShapeIdPairList().get(0).getShapeId();
        }
        return -1;
    }

    private static int fontPt100(HWPFile hwp, int shapeId) {
        var csList = hwp.getDocInfo().getCharShapeList();
        if (shapeId >= 0 && shapeId < csList.size()) {
            return csList.get(shapeId).getBaseSize();
        }
        return 1000;
    }

    private static int charSpaceHangul(HWPFile hwp, int shapeId) {
        var csList = hwp.getDocInfo().getCharShapeList();
        if (shapeId >= 0 && shapeId < csList.size() && csList.get(shapeId).getCharSpaces() != null) {
            return csList.get(shapeId).getCharSpaces().getHangul();
        }
        return 0;
    }

    private static int charRatioHangul(HWPFile hwp, int shapeId) {
        var csList = hwp.getDocInfo().getCharShapeList();
        if (shapeId >= 0 && shapeId < csList.size() && csList.get(shapeId).getRatios() != null) {
            int r = csList.get(shapeId).getRatios().getHangul();
            return r > 0 ? r : 100;
        }
        return 100;
    }

    private static String paraAlignName(HWPFile hwp, Paragraph p) {
        if (p == null || p.getHeader() == null) return "Left";
        int psId = p.getHeader().getParaShapeId();
        var psList = hwp.getDocInfo().getParaShapeList();
        if (psId >= 0 && psId < psList.size()) {
            ParaShape ps = psList.get(psId);
            if (ps.getProperty1() != null && ps.getProperty1().getAlignment() != null) {
                return ps.getProperty1().getAlignment().name();
            }
        }
        return "Left";
    }

    private static int paraLineSpacePct(HWPFile hwp, Paragraph p) {
        if (p == null || p.getHeader() == null) return 160;
        int psId = p.getHeader().getParaShapeId();
        var psList = hwp.getDocInfo().getParaShapeList();
        if (psId >= 0 && psId < psList.size()) {
            int ls = psList.get(psId).getLineSpace();
            if (ls >= 100 && ls <= 300) return ls;
        }
        return 160;
    }

    private static String inspectToJson(HWPFile hwp) {
        var csList = hwp.getDocInfo().getCharShapeList();
        int defaultShapeId = -1;
        int defaultFontPt100 = 0;
        int fallbackShapeId = -1;
        int fallbackFontPt100 = 0;
        for (int i = 0; i < csList.size(); i++) {
            int sz = csList.get(i).getBaseSize();
            if (defaultShapeId < 0 && sz >= 800 && sz <= 1100) {
                defaultShapeId = i;
                defaultFontPt100 = sz;
            }
            if (fallbackShapeId < 0 && sz >= 700) {
                fallbackShapeId = i;
                fallbackFontPt100 = sz;
            }
        }
        if (defaultShapeId < 0) {
            defaultShapeId = fallbackShapeId;
            defaultFontPt100 = fallbackFontPt100;
        }

        StringBuilder sb = new StringBuilder(4096);
        sb.append("{\"default_char_shape_id\":").append(defaultShapeId)
          .append(",\"default_font_pt100\":").append(defaultFontPt100)
          .append(",\"paragraphs\":[");
        boolean firstPara = true;
        List<Map<String, Object>> tableEntries = new ArrayList<>();

        var sections = hwp.getBodyText().getSectionList();
        for (int sIdx = 0; sIdx < sections.size(); sIdx++) {
            Section sec = sections.get(sIdx);
            for (int pIdx = 0; pIdx < sec.getParagraphCount(); pIdx++) {
                Paragraph p = sec.getParagraph(pIdx);
                int pShape = paraCharShapeId(p);
                int pPt = fontPt100(hwp, pShape);
                if (!firstPara) sb.append(',');
                firstPara = false;
                sb.append("{\"section\":").append(sIdx)
                  .append(",\"para\":").append(pIdx)
                  .append(",\"char_shape_id\":").append(pShape)
                  .append(",\"font_pt100\":").append(pPt)
                  .append(",\"char_space\":").append(charSpaceHangul(hwp, pShape))
                  .append(",\"char_ratio\":").append(charRatioHangul(hwp, pShape))
                  .append(",\"para_align\":").append(jsonEscape(paraAlignName(hwp, p)))
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
                    var pl = cell.getParagraphList();
                    int cShape = pl.getParagraphCount() > 0 ? paraCharShapeId(pl.getParagraph(0)) : -1;
                    int cPt = fontPt100(hwp, cShape);
                    int cSp = charSpaceHangul(hwp, cShape);
                    int cRt = charRatioHangul(hwp, cShape);
                    int cLs = pl.getParagraphCount() > 0 ? paraLineSpacePct(hwp, pl.getParagraph(0)) : 160;
                    if (!firstCell) sb.append(',');
                    firstCell = false;
                    sb.append("{\"cell_idx\":").append(cellSeq++)
                      .append(",\"row\":").append(lh.getRowIndex())
                      .append(",\"col\":").append(lh.getColIndex())
                      .append(",\"row_span\":").append(lh.getRowSpan())
                      .append(",\"col_span\":").append(lh.getColSpan())
                      .append(",\"width\":").append(lh.getWidth())
                      .append(",\"height\":").append(lh.getHeight())
                      .append(",\"margin_left\":").append(lh.getLeftMargin())
                      .append(",\"margin_right\":").append(lh.getRightMargin())
                      .append(",\"margin_top\":").append(lh.getTopMargin())
                      .append(",\"margin_bottom\":").append(lh.getBottomMargin())
                      .append(",\"char_shape_id\":").append(cShape)
                      .append(",\"font_pt100\":").append(cPt)
                      .append(",\"char_space\":").append(cSp)
                      .append(",\"char_ratio\":").append(cRt)
                      .append(",\"line_space_pct\":").append(cLs)
                      .append(",\"para_aligns\":[");
                    for (int k = 0; k < pl.getParagraphCount(); k++) {
                        if (k > 0) sb.append(',');
                        sb.append(jsonEscape(paraAlignName(hwp, pl.getParagraph(k))));
                    }
                    sb.append("],\"para_pt100s\":[");
                    for (int k = 0; k < pl.getParagraphCount(); k++) {
                        if (k > 0) sb.append(',');
                        sb.append(fontPt100(hwp, paraCharShapeId(pl.getParagraph(k))));
                    }
                    sb.append("],\"text\":").append(jsonEscape(cellJoinedText(cell))).append('}');
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
        Map<String, Integer> derivedCharShapes = new LinkedHashMap<>();
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
                int overrideShapeId = ed.get("override_char_shape_id") instanceof Number n ? n.intValue() : -1;

                if (tIdx < 0 || tIdx >= tables.size()) continue;
                Cell cell = findCell(tables.get(tIdx), row, col);
                if (cell == null) continue;
                ParagraphListInterface plist = cell.getParagraphList();
                if ("add_para".equals(mode)) {
                    if (addParagraphToList(plist, value)) applied++;
                    continue;
                }
                if (cpIdx < 0 || cpIdx >= plist.getParagraphCount()) continue;
                Paragraph targetP = plist.getParagraph(cpIdx);
                int finalShapeId = resolveDerivedCharShapeId(hwp, targetP, overrideShapeId, ed, derivedCharShapes);
                if (applyParaEdit(targetP, mode, anchor, value)) {
                    applyCharShapeOverride(hwp, targetP, finalShapeId);
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
                int overrideShapeId = ed.get("override_char_shape_id") instanceof Number n ? n.intValue() : -1;

                var secs = hwp.getBodyText().getSectionList();
                if (secIdx < 0 || secIdx >= secs.size()) continue;
                Section sec = secs.get(secIdx);
                if (pIdx < 0 || pIdx >= sec.getParagraphCount()) continue;
                Paragraph targetP = sec.getParagraph(pIdx);
                int finalShapeId = resolveDerivedCharShapeId(hwp, targetP, overrideShapeId, ed, derivedCharShapes);
                if (applyParaEdit(targetP, mode, anchor, value)) {
                    applyCharShapeOverride(hwp, targetP, finalShapeId);
                    applied++;
                }
            }
        }
        return applied;
    }

    private static int resolveDerivedCharShapeId(
            HWPFile hwp,
            Paragraph p,
            int baseOverrideSid,
            Map<String, Object> ed,
            Map<String, Integer> derivedCache
    ) throws Exception {
        int fitPt = ed.get("fit_font_pt100") instanceof Number n ? n.intValue() : -1;
        boolean hasSp = ed.get("fit_char_space") instanceof Number;
        int fitSp = hasSp ? ((Number) ed.get("fit_char_space")).intValue() : 0;
        if (fitPt <= 0 && !hasSp) {
            return baseOverrideSid;
        }
        var csList = hwp.getDocInfo().getCharShapeList();
        if (csList.isEmpty()) {
            return baseOverrideSid;
        }
        int baseSid = baseOverrideSid >= 0 ? baseOverrideSid : paraCharShapeId(p);
        if (baseSid < 0 || baseSid >= csList.size()) {
            baseSid = 0;
        }
        CharShape orig = csList.get(baseSid);
        int origPt = orig.getBaseSize();
        int origSp = orig.getCharSpaces() != null ? orig.getCharSpaces().getHangul() : 0;
        int targetPt = fitPt > 0 ? Math.max(800, fitPt) : origPt;
        int targetSp = hasSp ? Math.max(-10, fitSp) : origSp;
        if (targetPt == origPt && targetSp == origSp) {
            return baseOverrideSid;
        }
        String cacheKey = baseSid + ":" + targetPt + ":" + targetSp;
        Integer cached = derivedCache.get(cacheKey);
        if (cached != null) {
            return cached;
        }
        CharShape copy = orig.clone();
        copy.setBaseSize(targetPt);
        if (copy.getCharSpaces() != null) {
            copy.getCharSpaces().setForAll((byte) targetSp);
        }
        csList.add(copy);
        int newId = csList.size() - 1;
        hwp.getDocInfo().getIDMappings().setCharShapeCount(csList.size());
        derivedCache.put(cacheKey, newId);
        return newId;
    }

    private static void applyCharShapeOverride(HWPFile hwp, Paragraph p, int overrideShapeId) {
        if (overrideShapeId < 0) return;
        var csList = hwp.getDocInfo().getCharShapeList();
        if (overrideShapeId >= csList.size()) return;
        p.deleteCharShape();
        p.createCharShape();
        p.getCharShape().addParaCharShape(0, overrideShapeId);
        p.getHeader().setCharShapeCount(1);
        int newSz = csList.get(overrideShapeId).getBaseSize();
        if (p.getLineSeg() != null && !p.getLineSeg().getLineSegItemList().isEmpty()) {
            for (LineSegItem item : p.getLineSeg().getLineSegItemList()) {
                if (item.getLineHeight() != newSz) {
                    item.setLineHeight(newSz);
                    item.setTextPartHeight(newSz);
                    item.setDistanceBaseLineToLineVerticalPosition((int) (newSz * 0.85));
                }
            }
        }
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

    private static int displayWidthSpaces(String s) {
        int w = 0;
        for (int i = 0; i < s.length(); i++) {
            w += s.charAt(i) > 0x7F ? 2 : 1;
        }
        return w;
    }

    private static int slotCharWidth(HWPChar ch) {
        if (ch.getType() != HWPCharType.Normal) return 0;
        int code = ((HWPCharNormal) ch).getCode();
        if (code == 0x3000) return 2;
        if (code == 32 || code == 95 || code == 160) return 1;
        return 0;
    }

    private static int measureSlotWidthAfter(ParaText pt, int startIdx) {
        ArrayList<HWPChar> chars = pt.getCharList();
        int w = 0;
        for (int i = Math.max(0, startIdx); i < chars.size(); i++) {
            int cw = slotCharWidth(chars.get(i));
            if (cw == 0) break;
            w += cw;
        }
        return w;
    }

    private static void trimSlotCharsAfter(ParaText pt, int startIdx, int targetWidth, int minKeepWidth) {
        int avail = measureSlotWidthAfter(pt, startIdx);
        ArrayList<HWPChar> chars = pt.getCharList();
        int trimmed = 0;
        int toRemove = 0;
        for (int i = Math.max(0, startIdx); i < chars.size(); i++) {
            int cw = slotCharWidth(chars.get(i));
            if (cw == 0 || trimmed >= targetWidth || (avail - (trimmed + cw)) < minKeepWidth) {
                break;
            }
            trimmed += cw;
            toRemove++;
        }
        for (int k = 0; k < toRemove; k++) {
            chars.remove(startIdx);
        }
    }

    private static void trimLeadingSpacesBefore(ParaText pt, int endIdx, int targetWidth) {
        ArrayList<HWPChar> chars = pt.getCharList();
        int idx = Math.min(endIdx, chars.size()) - 1;
        int trimmed = 0;
        while (idx >= 0 && trimmed < targetWidth) {
            int cw = slotCharWidth(chars.get(idx));
            if (cw == 0) break;
            chars.remove(idx);
            trimmed += cw;
            idx--;
        }
    }

    private static int findNormalCharAfter(ParaText pt, int startIdx, char target) {
        ArrayList<HWPChar> chars = pt.getCharList();
        for (int i = Math.max(0, startIdx); i < chars.size(); i++) {
            HWPChar ch = chars.get(i);
            if (ch.getType() == HWPCharType.Normal && ((HWPCharNormal) ch).getCode() == target) {
                return i;
            }
        }
        return -1;
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
            boolean hasLeadingSlots = idx < pt.getCharList().size() && slotCharWidth(pt.getCharList().get(idx)) > 0;
            String ins = hasLeadingSlots ? value : (value + "   ");
            pt.insertString(idx, ins);
            if (hasLeadingSlots) {
                trimSlotCharsAfter(pt, idx + ins.length(), displayWidthSpaces(ins), 1);
            }
            ok = true;
        } else if ("after_anchor".equals(mode) && !anchor.isEmpty()) {
            int idx = findCharIndexOfSubstringEnd(pt, anchor);
            if (idx >= 0) {
                String ins = " " + value;
                pt.insertString(idx, ins);
                trimSlotCharsAfter(pt, idx + ins.length(), displayWidthSpaces(ins), 1);
                ok = true;
            }
        } else if ("date".equals(mode)) {
            Matcher m = Pattern.compile("(\\d{4})년\\s*(\\d{1,2})월\\s*(\\d{1,2})일").matcher(value);
            if (m.find()) {
                String yr = m.group(1), mo = m.group(2), dy = m.group(3);
                int idxM = findCharIndexOfSubstringEnd(pt, "월");
                if (idxM < 0) idxM = findCharIndexOfSubstringEnd(pt, "윌");
                int idxYEnd = findCharIndexOfSubstringEnd(pt, "년");
                int idxYStart = findCharIndexOfSubstringStart(pt, "년");
                if (idxM >= 0 && idxYEnd >= 0 && idxYStart >= 0) {
                    int availMd = measureSlotWidthAfter(pt, idxM);
                    String insD = (availMd >= dy.length() + 2) ? (" " + dy) : dy;
                    pt.insertString(idxM, insD);
                    trimSlotCharsAfter(pt, idxM + insD.length(), displayWidthSpaces(insD), 1);

                    int availYm = measureSlotWidthAfter(pt, idxYEnd);
                    String insM = (availYm >= mo.length() + 2) ? (" " + mo) : mo;
                    pt.insertString(idxYEnd, insM);
                    trimSlotCharsAfter(pt, idxYEnd + insM.length(), displayWidthSpaces(insM), 1);

                    idxYStart = findCharIndexOfSubstringStart(pt, "년");
                    if (idxYStart >= 0) {
                        trimLeadingSpacesBefore(pt, idxYStart, yr.length());
                        idxYStart = findCharIndexOfSubstringStart(pt, "년");
                        if (idxYStart >= 0) {
                            pt.insertString(idxYStart, yr);
                            ok = true;
                        }
                    }
                }
            }
        } else if ("date20".equals(mode)) {
            Matcher m = Pattern.compile("(\\d{4})년\\s*(\\d{1,2})월\\s*(\\d{1,2})일").matcher(value);
            if (m.find()) {
                String yr2 = m.group(1).substring(2), mo = m.group(2), dy = m.group(3);
                int idxM = findCharIndexOfSubstringEnd(pt, "월");
                if (idxM < 0) idxM = findCharIndexOfSubstringEnd(pt, "윌");
                int idxYEnd = findCharIndexOfSubstringEnd(pt, "년");
                int idx20End = findCharIndexOfSubstringEnd(pt, "20");
                if (idxM >= 0 && idxYEnd >= 0 && idx20End >= 0) {
                    int availMd = measureSlotWidthAfter(pt, idxM);
                    String insD = (availMd >= dy.length() + 2) ? (" " + dy) : dy;
                    pt.insertString(idxM, insD);
                    trimSlotCharsAfter(pt, idxM + insD.length(), displayWidthSpaces(insD), 1);

                    int availYm = measureSlotWidthAfter(pt, idxYEnd);
                    String insM = (availYm >= mo.length() + 2) ? (" " + mo) : mo;
                    pt.insertString(idxYEnd, insM);
                    trimSlotCharsAfter(pt, idxYEnd + insM.length(), displayWidthSpaces(insM), 1);

                    pt.insertString(idx20End, yr2);
                    trimSlotCharsAfter(pt, idx20End + yr2.length(), displayWidthSpaces(yr2), 1);
                    ok = true;
                }
            }
        } else if ("date_dot".equals(mode)) {
            Matcher m = Pattern.compile("(\\d{4})년\\s*(\\d{1,2})월\\s*(\\d{1,2})일").matcher(value);
            if (m.find()) {
                String yr = m.group(1), mo = m.group(2), dy = m.group(3);
                String prefix = !anchor.isEmpty() ? anchor : "202";
                int idxPrefixEnd = findCharIndexOfSubstringEnd(pt, prefix);
                if (idxPrefixEnd >= 0) {
                    int idxDot1 = findNormalCharAfter(pt, idxPrefixEnd, '.');
                    int idxDot2 = idxDot1 >= 0 ? findNormalCharAfter(pt, idxDot1 + 1, '.') : -1;
                    if (idxDot1 >= 0 && idxDot2 >= 0) {
                        int posAfter2 = idxDot2 + 1;
                        int idxDot3 = findNormalCharAfter(pt, posAfter2, '.');
                        int availMd = measureSlotWidthAfter(pt, posAfter2);
                        String insD = (idxDot3 >= 0 && availMd < dy.length() + 2) ? dy : (" " + dy);
                        pt.insertString(posAfter2, insD);
                        trimSlotCharsAfter(pt, posAfter2 + insD.length(), displayWidthSpaces(insD), idxDot3 >= 0 ? 1 : 0);

                        int posAfter1 = idxDot1 + 1;
                        int availYm = measureSlotWidthAfter(pt, posAfter1);
                        String insM = (availYm >= mo.length() + 2) ? (" " + mo) : mo;
                        pt.insertString(posAfter1, insM);
                        trimSlotCharsAfter(pt, posAfter1 + insM.length(), displayWidthSpaces(insM), 1);

                        String yrSuf = yr.length() > prefix.length() ? yr.substring(prefix.length()) : yr;
                        pt.insertString(idxPrefixEnd, yrSuf);
                        trimSlotCharsAfter(pt, idxPrefixEnd + yrSuf.length(), yrSuf.length(), 1);
                        ok = true;
                    }
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
