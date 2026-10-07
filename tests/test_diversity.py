import json
import unittest

from studio.diversity import CATEGORIES, image_prompt, make_planning_prompt, order_concepts, validate_concepts


def concept(**changes):
    value = {
        "title": "Xưởng đóng đàn ven sông",
        "category": "Xưởng thủ công",
        "subject": "Một cây đàn cello đang được ráp trên bàn thợ",
        "scene": "Xưởng gỗ ven sông với cửa lớn nhìn ra cầu đá",
        "story": "Dụng cụ và các mảnh gỗ cho thấy một nhạc cụ sắp hoàn thành",
        "composition": "Bàn thợ chéo tiền cảnh, cello ở giữa, kệ gỗ và cầu đá ở hậu cảnh",
        "palette": "Gỗ mật ong, xanh lam nhạt và đồng",
        "materials": "Gỗ phong, dây kim loại, kính và đá",
        "key": "cello making workshop beside river bridge",
        "prompt": "A sunlit cello maker workshop beside a river, tools and shaped wood across a diagonal workbench.",
    }
    value.update(changes)
    return value


class DiversityTests(unittest.TestCase):
    def test_exact_renamed_fruit_context_is_rejected(self):
        old = concept(
            title="Bát mơ bên cửa sổ bếp",
            category="Ẩm thực và nguyên liệu",
            subject="Bát gốm đầy quả mơ",
            scene="Bếp sáng bên cửa sổ nhìn ra vườn",
            story="Trái cây vừa hái đặt trên bàn chờ làm mứt",
            key="apricot bowl in sunny kitchen window",
        )
        new = concept(
            title="Bát cam bên cửa sổ bếp",
            category="Ẩm thực và nguyên liệu",
            subject="Bát gốm đầy quả cam",
            scene="Bếp sáng bên cửa sổ nhìn ra vườn",
            story="Trái cây vừa hái đặt trên bàn chờ làm mứt",
            key="orange bowl in sunny kitchen window",
        )
        accepted, errors = validate_concepts({"concepts": [new]}, [old], 1)
        self.assertEqual([], accepted)
        self.assertTrue(any("gần trùng" in error for error in errors))

    def test_materially_different_context_is_accepted(self):
        old = concept(title="Bát mơ", subject="Bát quả mơ", scene="Bếp bên cửa sổ", story="Chuẩn bị làm mứt", key="fruit bowl kitchen jam")
        accepted, errors = validate_concepts({"concepts": [concept()]}, [old], 1)
        self.assertEqual(1, len(accepted), errors)
        self.assertIn("600x900", accepted[0]["prompt"])
        self.assertIn("many medium-to-large distinct objects", accepted[0]["prompt"])
        self.assertNotIn("all four quadrants", accepted[0]["prompt"])

    def test_repeated_real_catalogue_visual_motifs_are_rejected(self):
        pairs = [
            (
                concept(title="Bản đồ sao", subject="Kính thiên văn trên ban công", scene="Đài quan sát nhỏ", key="telescope observatory terrace stars"),
                concept(title="Chiếc diều", subject="Kính thiên văn nhỏ", scene="Ban công đài quan sát trên đồi", key="small observatory telescope hilltop"),
            ),
            (
                concept(title="Bến hồ", subject="Canoe gỗ cập bến", scene="Cầu gỗ trên hồ", key="wooden canoe mountain lake dock"),
                concept(title="Bến sậy", subject="Thuyền chèo nhỏ bên bến gỗ", scene="Hồ nước trong", key="small rowboat clean wooden dock"),
            ),
            (
                concept(title="Góc đọc", subject="Ghế bành bên cửa sổ", scene="Phòng đọc có cửa sổ lớn", key="reading chair lakeside window"),
                concept(title="Góc nắng", subject="Ghế mây với khăn gấp", scene="Phòng nghỉ có cửa sổ rộng", key="rattan chair sunny window room"),
            ),
            (
                concept(title="Lối rừng", subject="Lối lát đá dưới tán cây", scene="Khu rừng xanh", key="woodland stone path leafy canopy"),
                concept(title="Mùa thu", subject="Con đường lát đá", scene="Rừng phong lá vàng", key="maple forest stone path autumn"),
            ),
            (
                concept(title="Mâm lê", subject="Đĩa lê chín trên bàn", scene="Bàn ăn cạnh cửa sổ", key="ripe pears bright window table"),
                concept(title="Bát hồng", subject="Bát gốm đựng hồng chín", scene="Bàn ăn cạnh cửa sổ", key="ripe persimmons ceramic bowl window table"),
            ),
        ]
        for old, new in pairs:
            with self.subTest(old=old["title"], new=new["title"]):
                accepted, errors = validate_concepts({"concepts": [new]}, [old], 1)
                self.assertEqual([], accepted)
                self.assertTrue(any("lặp mô-típ hình ảnh" in error for error in errors), errors)

    def test_layout_formula_can_repeat_across_changed_main_subjects(self):
        cases = [
            (
                concept(subject="Khinh khí cầu màu kem", scene="Thung lũng xanh có sông cong", key="hot-air balloon green valley winding river"),
                concept(subject="Máy bay lượn", scene="Thung lũng xanh với sông uốn", key="glider aircraft green valley river"),
            ),
            (
                concept(subject="Đàn hạc đứng cạnh ghế", scene="Phòng nhạc có cửa sổ cao", key="concert harp chair tall window music room"),
                concept(subject="Đàn cello tựa bên ghế", scene="Phòng hòa nhạc với cửa sổ cao", key="cello chair window rehearsal room"),
            ),
            (
                concept(subject="Chòi gỗ nhỏ giữa rừng", scene="Lối đi lát phiến đá sạch xuyên qua cỏ", key="woodland cabin grassy stone path"),
                concept(subject="Nhà kho nhỏ trong vườn", scene="Lối đá dẫn tới nhà kho giữa vườn táo", key="small painted barn grassy farm path"),
            ),
            (
                concept(subject="Thuyền buồm neo trong vịnh xanh", scene="Vịnh dưới vách đá sáng", key="sailboat clear cove cliffs"),
                concept(subject="Vịnh biển trong ôm bãi cát", scene="Bãi nhỏ giữa vách đá mượt", key="clear coastal cove smooth cliffs"),
            ),
            (
                concept(subject="Tiệm bánh dưới mái hiên", scene="Góc phố pastel lát đá", key="pastel bakery street awning"),
                concept(subject="Quầy sách cũ nhỏ dưới mái hiên", scene="Cửa hàng sách mở ra con ngõ lát đá", key="bookstore awning quiet lane"),
            ),
            (
                concept(subject="Cá koi trong hồ vườn trong vắt", scene="Lá sen lớn bên mép đá", key="koi clear garden pond large lily pads"),
                concept(subject="Hồ súng nhỏ với hoa trắng", scene="Vườn nước có vài lá lớn", key="water lily clear garden pond large pads"),
            ),
        ]
        for old, new in cases:
            accepted, errors = validate_concepts({"concepts": [new]}, [old], 1)
            self.assertEqual([], accepted)
            self.assertTrue(any("lặp mô-típ hình ảnh" in error for error in errors), errors)

    def test_broad_subject_family_is_allowed_in_distinct_context(self):
        old = concept(subject="Thuyền chèo nhỏ bên bến gỗ", scene="Hồ nước trong", key="rowboat wooden dock lake")
        different_boat = concept(
            title="Buồm ngoài khơi",
            subject="Thuyền buồm căng gió giữa biển mở",
            scene="Ngoài khơi dưới dải mây dài",
            story="Một chuyến vượt biển trong ngày nắng",
            composition="Cánh buồm lớn trên đường chân trời rộng",
            key="sailboat open ocean wind crossing",
        )
        accepted, errors = validate_concepts({"concepts": [different_boat]}, [old], 1)
        self.assertEqual(1, len(accepted), errors)

    def test_empty_pottery_bowl_by_window_is_not_classified_as_fruit_still_life(self):
        old = concept(subject="Bát gốm trống bên cửa sổ", scene="Bàn ăn cạnh cửa sổ", key="empty ceramic bowl window table")
        fruit = concept(
            title="Mâm lê",
            subject="Đĩa lê chín trên bàn",
            scene="Bàn ăn cạnh cửa sổ",
            story="Bữa quả nhẹ buổi chiều",
            key="ripe pears bright window table",
        )
        accepted, errors = validate_concepts({"concepts": [fruit]}, [old], 1)
        self.assertEqual(1, len(accepted), errors)

    def test_within_batch_duplicate_is_rejected(self):
        first = concept()
        second = concept(title="Nghệ nhân cello", key="cello crafting workshop next to river bridge")
        accepted, errors = validate_concepts({"concepts": [first, second]}, [], 2)
        self.assertEqual(1, len(accepted))
        self.assertTrue(any("concept 2" in error and "gần trùng" in error for error in errors))

    def test_malformed_responses_are_rejected(self):
        self.assertEqual([], validate_concepts("not json", [], 2)[0])
        self.assertEqual([], validate_concepts({"items": []}, [], 2)[0])
        broken = concept()
        del broken["story"]
        accepted, errors = validate_concepts({"concepts": [broken]}, [], 2)
        self.assertEqual([], accepted)
        self.assertTrue(any("story" in error for error in errors))

    def test_requested_limit_is_enforced(self):
        values = [concept(title=f"Cảnh {n}", subject=f"Máy chuyên dụng {n}", scene=f"Địa điểm riêng {n}", story=f"Câu chuyện khác {n}", key=f"unique machine setting story {n}") for n in range(3)]
        accepted, errors = validate_concepts({"concepts": values}, [], 2)
        self.assertLessEqual(len(accepted), 2)
        self.assertTrue(any("tối đa 2" in error for error in errors))

    def test_prompt_has_schema_style_and_compact_history(self):
        history = [concept(title=f"Mục {n}", category=CATEGORIES[n % len(CATEGORIES)], key=f"semantic-{n}") for n in range(1000)]
        prompt = make_planning_prompt(12, history)
        self.assertIn("chính xác 12", prompt)
        self.assertIn('{"concepts":[...]}', prompt)
        self.assertIn("600x900", prompt)
        self.assertIn("không người", prompt)
        self.assertIn("Đổi loại trái cây", prompt)
        self.assertIn("MÔ-TÍP HÌNH ẢNH ĐÃ DÙNG", prompt)
        self.assertIn("S:", prompt)
        self.assertIn("C:", prompt)
        self.assertIn("B:", prompt)
        self.assertIn("Màu:", prompt)
        self.assertIn("Chất:", prompt)
        self.assertLess(len(prompt), 25000)
        for category in CATEGORIES:
            self.assertIn(category, prompt)

    def test_full_history_motif_summary_includes_entries_outside_sample(self):
        oldest = concept(
            title="Bản đồ sao cũ",
            subject="Kính thiên văn trên ban công",
            scene="Đài quan sát nhỏ",
            key="telescope observatory terrace",
        )
        history = [oldest] + [
            concept(
                title=f"Máy riêng {n}",
                subject=f"Thiết bị khảo sát alpha {n}",
                scene=f"Phòng beta {n}",
                key=f"unique survey machine station {n}",
            )
            for n in range(200)
        ]
        prompt = make_planning_prompt(3, history)
        self.assertIn("kính thiên văn tại đài quan sát: đã dùng 1 lần", prompt)
        self.assertIn("Bản đồ sao cũ", prompt)

    def test_unknown_real_category_is_preserved(self):
        item = concept(category="Nghệ thuật giấy dân gian")
        accepted, errors = validate_concepts({"concepts": [item]}, [], 1)
        self.assertEqual("Nghệ thuật giấy dân gian", accepted[0]["category"], errors)

    def test_order_keeps_every_entry_and_avoids_adjacent_category_when_possible(self):
        a = concept(title="A", key="a unique", category="Xưởng thủ công", palette="đỏ", composition="cận cảnh")
        b = concept(title="B", key="b unique", category="Xưởng thủ công", palette="xanh", composition="toàn cảnh")
        c = concept(title="C", key="c unique", category="Động vật", palette="vàng", composition="trên cao")
        ordered = order_concepts([a, b, c])
        self.assertCountEqual([id(a), id(b), id(c)], [id(item) for item in ordered])
        self.assertNotEqual(ordered[0]["category"], ordered[1]["category"])

    def test_image_prompt_is_idempotent(self):
        once = image_prompt(concept())
        twice = image_prompt({"prompt": once})
        self.assertEqual(once, twice)


if __name__ == "__main__":
    unittest.main()
