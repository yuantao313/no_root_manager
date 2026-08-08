"""用户名生成器测试：单姓/复姓/多音字/空输入。"""

from accounts.username_gen import generate_username_groups, generate_usernames, split_name


class TestSplitName:
    def test_single_surname(self):
        assert split_name("张三丰") == ("张", "三丰", False)

    def test_compound_surname(self):
        assert split_name("诸葛孔明") == ("诸葛", "孔明", True)

    def test_two_char_name_not_compound(self):
        assert split_name("张伟") == ("张", "伟", False)

    def test_empty(self):
        assert split_name("") == ("", "", False)


class TestGenerateUsernames:
    def test_single_surname_examples(self):
        out = generate_usernames("张三丰")
        for expect in ("zhangsanfeng", "sanfengzhang", "zhangsf", "sfzhang"):
            assert expect in out

    def test_compound_surname_examples(self):
        out = generate_usernames("诸葛孔明")
        for expect in ("zhugekongming", "kongmingzhuge", "zhugekm", "kmzhuge"):
            assert expect in out

    def test_compound_also_has_single_split(self):
        out = generate_usernames("诸葛孔明")
        assert "gekongmingzhu" in out
        assert "zhugkm" in out
        assert "gkmzhu" in out

    def test_polyphonic_all_combinations(self):
        out = generate_usernames("曾雅")
        assert "cengya" in out
        assert "zengya" in out

    def test_three_reading_polyphonic(self):
        out = generate_usernames("单立人")
        assert "danliren" in out
        assert "chanliren" in out
        assert "shanliren" in out

    def test_empty_name(self):
        assert generate_usernames("") == []
        assert generate_usernames("   ") == []

    def test_deduplicated(self):
        out = generate_usernames("张三丰")
        assert len(out) == len(set(out))


class TestGenerateGroups:
    def test_group_structure(self):
        g = generate_username_groups("诸葛孔明")
        assert g["is_compound_surname"] is True
        assert g["compound_surname"]
        assert g["single_surname"]
        assert g["suggestions"] == list(dict.fromkeys(g["single_surname"] + g["compound_surname"]))

    def test_single_group_structure(self):
        g = generate_username_groups("张三丰")
        assert g["is_compound_surname"] is False
        assert g["compound_surname"] == []
        assert g["suggestions"] == g["single_surname"]
