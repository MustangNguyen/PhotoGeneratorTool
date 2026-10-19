"""Planner themes: code -> (category label, target share %).

These replaced the 10 themes distilled from the Final sample library, whose content the
catalogue had exhausted.  Final still sets the palette and style (studio/final.py keeps
its own theme codes for final-index.json); content now follows these themes.
"""

THEMES = {
    'hobby': ('Bàn sở thích và xưởng thủ công', 10),
    'music': ('Nhạc cụ và không gian âm nhạc', 8),
    'toys': ('Đồ chơi, trò chơi và mô hình', 9),
    'festival': ('Lễ hội, trang trí và dịp đặc biệt', 9),
    'nature': ('Núi, rừng, sông suối và thác', 9),
    'wildlife': ('Chim, côn trùng, sinh vật hoang dã và dưới nước', 9),
    'farm': ('Nông trại, vườn cây ăn trái và đồng quê', 8),
    'park': ('Công viên, khu vui chơi và sân chơi', 8),
    'discovery': ('Khám phá, bản đồ và khoa học tự nhiên', 7),
    'outdoor': ('Thể thao, cắm trại và dã ngoại', 8),
    'books': ('Thư viện, hiệu sách và văn phòng phẩm', 7),
    'structure': ('Công trình độc đáo: cầu, cối xay, lâu đài, nhà trên cây', 8),
}
CATEGORY_TO_THEME = {category: theme for theme, (category, _) in THEMES.items()}
