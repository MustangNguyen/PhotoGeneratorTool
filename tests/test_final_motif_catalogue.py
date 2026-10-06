import json
import unittest
from pathlib import Path

from studio.diversity import CONTENT_MOTIFS_PATH, _motifs, validate_concepts
from tests.test_diversity import concept


# Concrete scene descriptions, kept independent of the phrase lists in the catalogue.
CASES = [
    ('animal-drinking-shore', 'Con hươu cúi uống nước', 'Bờ suối trong', 'Trung cảnh ngang tầm mắt'),
    ('animal-resting-shore', 'Con hải cẩu nằm nghỉ', 'Bờ vịnh yên', 'Cận vừa ngang mặt đá'),
    ('bouquet-books-window', 'Bình hoa hồng trong veo', 'Bàn có sách mở cạnh cửa sổ', 'Cận cảnh tĩnh vật'),
    ('dessert-glass-window', 'Ly kem nhiều lớp', 'Quầy gỗ bên cửa sổ nhìn biển', 'Cận cảnh một ly'),
    ('pastry-coffee-table', 'Bánh sừng bò và tách cà phê', 'Bàn ăn sáng', 'Nhìn nghiêng'),
    ('coffee-beans-still-life', 'Ấm pha cà phê và hạt rang', 'Bàn bếp', 'Nhìn ngang'),
    ('plated-meal-overhead', 'Đĩa cơm gà cùng rau', 'Bàn ăn sáng', 'Nhìn từ trên xuống'),
    ('bird-flowering-branch', 'Chim nhỏ màu vàng', 'Đậu trên cành hoa nở', 'Chủ thể lớn lệch trái'),
    ('door-flower-frame', 'Cánh cửa xanh', 'Hoa giấy bao quanh lối vào', 'Chính diện'),
    ('mediterranean-arched-stair', 'Cầu thang lát đá', 'Địa Trung Hải với cửa vòm trắng', 'Các bậc dẫn lên cao'),
    ('vintage-car-seaside', 'Xe cổ màu xanh', 'Đỗ ở đường ven biển', 'Góc nhìn chéo'),
    ('sunny-laundry-plants', 'Máy giặt trắng', 'Phòng có cây xanh dưới mái kính', 'Chính diện'),
    ('succulent-shelves', 'Chậu sen đá', 'Xếp trên kệ gỗ', 'Các tầng rộng'),
    ('bath-window-plants', 'Bồn tắm trắng', 'Bên cửa sổ với chậu cây', 'Góc rộng'),
    ('stationery-desk', 'Bàn học có sổ tay và bút chì', 'Góc học tập gọn', 'Nhìn chéo từ trên'),
    ('vintage-audio-table', 'Máy hát đĩa bằng gỗ', 'Đặt trên bàn thấp', 'Góc chéo'),
    ('globe-bookshelf', 'Quả địa cầu cổ', 'Bên tủ sách gỗ', 'Chủ thể lớn'),
    ('train-interior-seats', 'Nội thất toa tàu điện', 'Dãy ghế trống sáng màu', 'Nhìn dọc lối giữa'),
    ('lighthouse-cliff', 'Hải đăng trắng', 'Trên mỏm đá bên biển', 'Góc rộng'),
    ('cottage-floral-facade', 'Mặt tiền nhà cổ', 'Bồn hoa cửa sổ nở rộ', 'Chính diện'),
    ('open-window-coast', 'Cửa sổ mở bằng gỗ', 'Nhìn xuống phố biển', 'Cửa làm khung'),
    ('tea-flowers-table', 'Tách trà cạnh bó hoa', 'Trên bàn gỗ sáng', 'Tĩnh vật gần'),
    ('tallship-ocean', 'Tàu buồm với cánh buồm trắng', 'Giữa biển xanh', 'Nhìn chéo mạn'),
    ('resort-pool', 'Hồ bơi xanh', 'Sân trong khách sạn', 'Nhìn từ ban công'),
    ('sleeping-pet-blanket', 'Cún ngủ cuộn tròn', 'Trong chăn xanh', 'Cận cảnh khuôn mặt'),
    ('stained-glass-nature', 'Ô kính màu với chim xanh', 'Tấm kính được nắng chiếu', 'Chính diện'),
]


class FinalMotifCatalogueTests(unittest.TestCase):
    def test_reviewed_scene_families_are_recognized_and_sources_exist(self):
        data = json.loads(CONTENT_MOTIFS_PATH.read_text())
        catalogue = {m['id']: m for m in data['motifs']}
        self.assertEqual(set(catalogue), {row[0] for row in CASES})
        root = CONTENT_MOTIFS_PATH.parent
        for motif_id, subject, scene, composition in CASES:
            with self.subTest(motif=motif_id):
                self.assertIn(catalogue[motif_id]['label'], _motifs(dict(subject=subject, scene=scene, composition=composition)))
                for relative in catalogue[motif_id]['evidence']:
                    self.assertTrue(relative.startswith('Final/'), relative)
                    # The private reference library is optional in public checkouts.
                    if (root / 'Final').exists():
                        self.assertTrue((root / relative).is_file(), relative)

    def test_same_scene_different_language_category_and_props_is_rejected(self):
        old = concept(title='Bàn trà hoa hồng', subject='Tách trà cạnh bó hoa', scene='Bàn gỗ', key='rose porcelain morning refreshments')
        new = concept(title='Quiet afternoon', category='New category', subject='A teacup and a flower vase', scene='A marble tabletop', key='evening jasmine porcelain arrangement')
        accepted, errors = validate_concepts({'concepts': [new]}, [old], 1)
        self.assertFalse(accepted)
        self.assertTrue(any('mô-típ' in e for e in errors))

    def test_different_setting_or_background_prop_is_not_same_formula(self):
        scenes = [
            ('Con hươu đi qua đồng cỏ', 'Đồi cỏ sáng', 'Trung cảnh', 'động vật cúi uống nước cạnh bờ'),
            ('Con cáo nằm nghỉ', 'Trong hốc cây khô', 'Cận vừa', 'động vật nghỉ cạnh mặt nước'),
            ('Chim bay', 'Trên biển rộng', 'Chim nhỏ phía xa', 'chim đậu giữa cành hoa'),
            ('Tách trà', 'Bàn gỗ trống bên bếp', 'Cận cảnh', 'tách trà cùng hoa trên bàn'),
            ('Bình gốm rỗng', 'Có bình hoa cạnh sách ở cửa sổ phía xa', 'Chủ thể lớn', 'bình hoa cạnh sách bên cửa sổ'),
            ('Xe cổ', 'Trong nhà trưng bày', 'Chính diện', 'xe cổ đỗ bên biển'),
            ('Chó đang chạy', 'Trên bãi cỏ, chăn ở xa', 'Góc thấp', 'thú cưng ngủ cuộn trong chăn'),
        ]
        for subject, scene, composition, label in scenes:
            with self.subTest(subject=subject):
                self.assertNotIn(label, _motifs(dict(subject=subject, scene=scene, composition=composition)))


if __name__ == '__main__':
    unittest.main()
