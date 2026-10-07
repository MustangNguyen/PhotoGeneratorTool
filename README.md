# Puzzle Atelier

Tool local tạo ảnh cho game ghép hình: nhập **số lượng**, tự lập context khác nhau, lọc trùng với lịch sử, tạo song song, duyệt và xuất ảnh 600×900.

## Chạy

Cần Python 3.10+ và Codex CLI đã đăng nhập. Không cần npm để chạy giao diện.

```bash
git clone https://github.com/MustangNguyen/PhotoGeneratorTool.git
cd PhotoGeneratorTool
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

Mở http://127.0.0.1:8787. Có thể chọn cổng qua `--port 8788`, thư mục dữ liệu qua `--data-dir /path/to/data`.
Trên Linux, `python launch.py` tự mở trình duyệt và khởi động server nếu chưa chạy. Shortcut cục bộ không nằm trong repo vì phụ thuộc đường dẫn máy.

Mặc định dùng **Codex CLI đã đăng nhập trên máy**. CLI phải hỗ trợ image generation; bản đã kiểm tra ở môi trường này là 0.160.1. Nếu chưa đăng nhập, chạy `codex login` trong terminal. Không lấy hoặc chuyển tiếp OAuth token vào API riêng.

1. Nhập 1–1.000 ảnh và bấm tạo.
2. Tool viết bản nháp theo nhóm tối đa 20, lọc trùng, rồi gọi một lượt AI duyệt cả nhóm. Mục tốt giữ nguyên; mục lỗi được sửa hoặc loại và bù ở lượt tiếp. Chỉ context qua duyệt và kiểm tra lại mới được lưu để tạo ảnh. Sau khi đủ context, mặc định tạo 4 ảnh đồng thời.
3. Trong cài đặt, có thể chọn từ 1 đến 8 ảnh đồng thời. Đây là số tác vụ chạy cùng lúc, không phải gửi toàn bộ lô 1.000 ảnh trong một lần.
4. Bảng hoạt động trực tiếp cho biết từng ảnh đang chuẩn bị, chờ dịch vụ trả ảnh, lưu file hay kiểm tra gần trùng, kèm thời gian đã chạy. Đây là trạng thái thực, không phải phần trăm ước đoán.
5. Khi bấm tạm dừng, các ảnh đang tạo sẽ hoàn tất rồi lô mới dừng; tiếp tục vẫn giữ nguyên ảnh đã xong.
6. Mở ảnh để theo dõi trạng thái, xem context, lưới 4–8, mảnh xáo trộn và duyệt/loại. Hộp chi tiết tự cập nhật mà không làm mất trạng thái preview.
7. Xuất ZIP chứa ảnh JPG dọc 600×900 và `manifest.json` (context, prompt, thứ tự, trạng thái duyệt).

## Đa dạng nội dung

10 nhóm chủ đề lấy từ kho Final kèm tỷ lệ mục tiêu (xem phần Bám phong cách Final), subject/scene/story/composition/palette/materials cho từng concept. Bộ lọc chặn trùng khóa, dấu vân tay từ vựng và một số thay đổi bề mặt như đổi trái cây trong cùng cảnh. Lịch sử của tất cả batch được so sánh, kể cả ảnh đã loại, để không tự dùng lại ý đó. Sắp xếp ưu tiên tránh nhóm/bảng màu/góc nhìn tương tự cạnh nhau.

Đây là **lọc gần trùng theo heuristic**, không phải chứng minh 1.000 ảnh khác nhau tuyệt đối về ngữ nghĩa. Sau gen có cảnh báo ảnh gần giống theo màu/bố cục thô. Người duyệt vẫn kiểm tra nội dung và trải nghiệm ghép. Không tự gen lại khi có cảnh báo để tránh tốn quota. Kho ảnh mẫu `Final/` không được phân phối trong repo; mô tả từng ảnh nằm trong `final-index.json` và được dùng để chặn concept lặp lại ảnh mẫu.

## Quota và kết nối

Codex CLI dùng quota theo tài khoản đang đăng nhập. Chạy song song không làm giảm quota: mỗi ảnh đầu ra vẫn dùng quota như bình thường. Mặc định 4 tác vụ giúp tăng tốc, nhưng mức phù hợp còn tùy giới hạn và độ ổn định của provider; nếu hay lỗi, giảm concurrency trong cài đặt. Không có cam kết tạo đủ 1.000 ảnh trong một hạn mức. Khi quota hoặc provider lỗi, batch dừng và giữ kết quả; tiếp tục sau hoặc thử lại riêng ảnh lỗi. Yêu cầu đang chạy khi mất điện/timeout có thể đã dùng quota; ứng dụng không tự gọi lại để tránh tính phí hai lần.

Trang lấy trạng thái mới từ máy chủ mỗi vài giây; đồng hồ của ảnh được cập nhật tại trình duyệt mỗi giây và không tạo thêm yêu cầu. Nếu mất kết nối, đồng hồ dừng ở lần cập nhật cuối và giao diện báo dữ liệu có thể đã cũ.

OpenAI API là lựa chọn trong cài đặt, **tính phí riêng**, không dùng quota ChatGPT/Codex. Chỉ được gọi khi người dùng chọn provider API và bấm chạy. API key lấy từ `OPENAI_API_KEY` hoặc lưu server-side trong `data/api-key` với quyền 0600; không trả key về trình duyệt. Model context phải hỗ trợ Responses structured outputs; model ảnh mặc định `gpt-image-2`, có thể đổi khi tài khoản hỗ trợ.

Nguồn tích hợp: [Codex image generation](https://learn.chatgpt.com/docs/image-generation), [Image API](https://developers.openai.com/api/docs/guides/image-generation), [giới hạn ChatGPT-plan OAuth](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations).

## Dữ liệu và giới hạn

- SQLite: `data/studio.sqlite3`; ảnh gốc + ảnh xuất + context: `data/images/<id>/`; log CLI: `data/jobs/` và thư mục ảnh tương ứng.
- Chỉ bind `127.0.0.1`; kiểm tra Host/Origin và JSON cho thao tác ghi; không phục vụ toàn bộ filesystem.
- Chạy một tiến trình ứng dụng cho mỗi data-dir.
- Lưới preview là khảo sát ô chữ nhật; chưa xác nhận cơ chế cắt/ghép thật của game.
- Đầu ra JPG (JPEG chất lượng 95) chuẩn hóa về đúng 600×900 dọc, không kéo méo, cắt giữa nếu tỷ lệ khác 2:3. Tải từng ảnh và xuất ZIP đều chuyển cả ảnh PNG cũ sang JPG. Giữ ảnh gốc để đối chiếu.
- Ảnh tham chiếu tùy chọn đặt tại `output/apricot-kitchen-source.png`; khi thiếu, giao diện ẩn khung tham chiếu. Ảnh mẫu và ảnh đã tạo không được phân phối trong repo.

## Kiểm tra

```bash
python3 -m unittest discover -s tests -v
node --check web/app.js
python3 -m py_compile app.py studio/*.py
```

Test dùng provider giả để kiểm tra trạng thái/persist/export mà không tiêu quota. Các kiểm tra không yêu cầu kho ảnh mẫu `Final/`.

## Cập nhật hướng nội dung từ feedback

`art-direction.json` là hướng dẫn biên tập dùng chung cho bước lên context và tạo ảnh. File được đọc lại ở mỗi yêu cầu nên các lần sửa nội dung tiếp theo không cần khởi động lại server. Các mục `planning_rules` (tiếng Việt) và `image_rules` (tiếng Anh) quy định hướng dẫn; `feedback` lưu ví dụ và lý do xác nhận.

Feedback hiện tại: không có bùn đất, nền đất trơ hoặc nước đục màu bùn; tránh màu xỉn và cảnh kém đẹp. Ưu tiên cảnh sạch, sáng đẹp, màu trong/tươi tự nhiên, vẫn đa dạng chủ đề. Đây là hướng dẫn cho model, không phải bộ nhận diện bùn tự động từ pixel; duyệt ảnh vẫn cần thiết.

Ảnh chưa bắt đầu sẽ nhận hướng dẫn hiện tại, kể cả context đã lập trước đó. Database/manifest lưu prompt thực tế đã gửi; `planned_prompt` giữ bản gốc để đối chiếu. Hướng dẫn mới thay thế block cũ khi thử lại, không chồng nhiều phiên bản.


### Cập nhật đa dạng nội dung

Bộ lọc trước khi tạo ảnh xét thêm các công thức cảnh dễ lặp (ví dụ ghế cạnh cửa sổ, thuyền cạnh bến, kính thiên văn trên sân quan sát), kể cả khi đổi tên, category hoặc câu chuyện. Prompt có tóm tắt motif từ toàn bộ lịch sử và mô tả chủ thể/cảnh/bố cục rõ hơn. Gợi ý chill không còn danh sách ví dụ cố định để model bám lặp. Bộ lọc vẫn là heuristic có giới hạn, không phải nhận diện ngữ nghĩa toàn diện hay bảo đảm 1.000 ảnh hoàn toàn khác nhau. Giữ quy trình duyệt ảnh; không tự tạo lại ảnh đã hoàn tất.


24 công thức bổ sung được quản lý trong `content-motifs.json`, dựa trên ảnh mẫu đã xem trong `Final/` (đường dẫn nguồn được lưu trong trường `evidence`; file ảnh không được phân phối). Cộng 11 công thức hiện có thành 35. Catalogue là bộ nhận diện chạy local, không phải danh sách bắt model tạo lần lượt và không tự đưa ảnh mẫu vào lịch sử. File được kiểm tra schema và nạp lại khi sửa; không phát sinh lượt gọi AI riêng để lọc.

### Giới hạn chủ thể chính và gợi ý từ Final

Trước đây model tự chọn chủ thể trong mỗi nhóm nên hay quay lại ý quen thuộc (xe đạp touring, thỏ tai cụp, bánh mì, ngô nướng…), còn bộ lọc từ vựng không bắt được khi chỉ đổi bối cảnh. Hiện tại:

1. Mỗi lượt lập context, ứng dụng gán cho từng concept một ảnh mẫu Final chưa dùng (theo GỢI Ý PHÂN BỔ). Model lấy chủ thể/ý chủ đạo của gợi ý rồi tự dựng cảnh mới và trả lại `seed_id`; context lưu `final_seed` để lượt sau không lặp gợi ý.
2. Model phải trả `main_subject`: danh từ tiếng Anh chung, số ít (ví dụ `rabbit`, `bicycle`). Mỗi chủ thể chỉ được dùng tối đa `1 + số context / 500` lần (756 mục → 2 lần, 1.000 mục → 3 lần); đổi giống, màu, tính từ hay bối cảnh vẫn tính là cùng chủ thể. Danh từ quá chung như `machine`, `table`, `tree` cần cả từ đứng trước trùng mới tính là một chủ thể.
3. Context cũ không có `main_subject` được suy ra từ cụm danh từ đầu của `key`. Cách suy ra này là heuristic và có thể sai với key không bắt đầu bằng chủ thể.
4. Prompt lập context liệt kê toàn bộ chủ thể đã đủ giới hạn và các chủ thể dùng nhiều nhất, thay vì chỉ vài chục dấu vân tay gần nhất.

### Trục đa dạng (`diversity-axes.json`)

Ngoài chủ thể, mỗi concept được gán thêm một số trục đa dạng. File hiện có 14 trục, 624 giá trị, chắt lọc từ caption 2.204 ảnh Final: góc nhìn và điểm đứng (48), kiểu bố cục và cách bày (46), thời điểm và ánh sáng (37), mùa/thời tiết/vụ mùa (40), dịp và lễ hội (46), vùng và địa danh (57), thời kỳ và phong cách (45), chất liệu (49), bảng màu (45), không gian (50), tình huống (48), điểm nhấn phụ (50), họa tiết (40), hình thức (23, weight thấp vì phần lớn là tranh/mô hình, trong Final chỉ chiếm vài phần trăm). File được sinh bằng tay một lần; sửa trực tiếp trong JSON.

- Mỗi concept chỉ nhận ngẫu nhiên từ `per_concept.min` đến `per_concept.max` trục (mặc định 2–4), không phải tất cả. Trục không được gán thì model tự chọn, miễn khác các concept còn lại.
- Trong mỗi trục được gán, ứng dụng chọn giá trị **ít được dùng nhất** trong lịch sử (hòa thì ngẫu nhiên), nên kho ảnh tự trải đều qua các giá trị.
- `weight` cao thì trục được chọn thường hơn. `categories` / `exclude_categories` (mã nhóm `food`, `interior`, `garden`, `objects`, `facade`, `animal`, `vehicle`, `shop`, `coast`, `drink`) giới hạn trục cho một số nhóm.
- Thêm trục hoặc giá trị bằng cách sửa file; ứng dụng đọc lại khi file đổi, không cần khởi động lại. File sai cấu trúc sẽ dừng batch với lỗi rõ ràng.
- Context lưu trục đã gán ở trường `axes`; bước duyệt nhận các trục này và phải giữ chúng khi sửa.

Tổ hợp không hợp nhau được khai báo trong `rules`, ứng dụng không bao giờ gán chúng cùng lúc:

- `exclude`: `if` và `then` không được cùng xảy ra, ví dụ tuyết + vùng nóng, góc nhìn ngoài trời + không gian trong nhà, vật liệu vải/giấy + công trình.
- `require`: khi `if` xảy ra thì `then` phải đúng nếu trục đó được gán, ví dụ Tết Nguyên đán chỉ đi với vùng Đông Á đón Tết âm lịch và đầu xuân, Diwali chỉ ở Ấn Độ, cực quang chỉ ở Na Uy/Scotland.
- Bộ chọn là `{"axis": "season", "values": [...]}` (bỏ `values` = mọi giá trị của trục) hoặc `{"category": ["coast"]}`. Trục/giá trị/mã nhóm sai tên sẽ báo lỗi khi đọc file.
- Nếu một trục không còn giá trị hợp lệ, ứng dụng bỏ trục đó và chọn trục khác.

Luật chỉ áp dụng cho trục được gán. Chủ thể gợi ý từ Final vẫn có thể không hợp trục (ví dụ hải đăng + Alps không còn xảy ra, nhưng gợi ý "cua trên bãi đá" + "trên tàu"): model được yêu cầu đổi chủ thể theo tinh thần gợi ý thay vì bỏ trục, và bước duyệt loại cảnh phi thực tế. Gặp cặp lạ lặp lại thì thêm luật mới.

## Bám phong cách Final

Hai file sinh ra một lần từ toàn bộ 2.204 ảnh trong `Final/` (Chap, Daily, lv; không gồm Collection và video):

- `final-style.json`: phân vị p5–p95 của độ bão hòa, độ sáng, độ ấm (R−B), độ rực màu, độ tương phản và mật độ chi tiết, đo bằng `studio/style.py`.
- `final-index.json`: mỗi ảnh có nhóm chủ đề, lưới cắt lấy từ tên file, mô tả ngắn và số đo màu. Mô tả do Claude viết khi xem contact sheet, không tốn quota tạo ảnh.

Cách dùng trong tool:

1. Quy cách ảnh (`_IMAGE_RULES`, prompt lập context và `art-direction.json` v11) mô tả phong cách Final: nắng ấm, màu tươi bão hòa hài hòa, cảnh bày biện nhiều vật cỡ vừa-lớn rõ ràng. Vẫn giữ không người, không chữ, không bùn, không blur, không chi tiết li ti.
2. Nhóm chủ đề và tỷ lệ mục tiêu lấy từ Final. Mỗi lần lập context, planner nhận gợi ý phân bổ theo nhóm đang thiếu và vài mô tả ảnh Final cùng nhóm làm ví dụ.
3. Concept có tiêu đề lặp lại mô tả một ảnh Final bị loại trước khi tạo ảnh. Khi chạy `python3 app.py` trên máy có `Final/`, ảnh mới còn được so pixel với ảnh mẫu; fingerprint lưu ở `data/final-fingerprints.json`, chỉ tính lại khi file đổi. Lần kiểm tra đầu tiên chậm hơn vì phải đọc toàn bộ ảnh mẫu.
4. Sau khi lưu ảnh, tool đo màu và so với `final-style.json`. Ảnh lệch xa (ví dụ nhạt và lạnh hơn hẳn Final) có dòng "Màu so với Final" trong hộp chi tiết. Đây chỉ là cảnh báo; tool không tự tạo lại. Ngưỡng `warn_score` 0,5 gắn cờ khoảng 13% chính ảnh Final và 29/50 ảnh tool tạo gần nhất.

## Model và cài đặt

Trong Cài đặt, nhập `gpt-6-luna` ở model context nếu tài khoản hỗ trợ. Bước lập context dùng reasoning `medium`; để trống model thì dùng model mặc định của Codex CLI. Tạo ảnh vẫn dùng imagegen tích hợp trong CLI. Cài đặt từng máy, API key, database và log nằm trong `data/` hoặc biến môi trường, không đưa vào Git.


## Luồng context có duyệt

Bản nháp mô tả cảnh đời thực trước: chủ thể, nơi chốn, vị trí, cấu tạo/điểm tựa và tình huống; sau đó mới chọn góc máy và tổ chức mảng màu. Không ép vật thể thành hình trang trí hoặc thêm đạo cụ để kể chuyện.

Mỗi nhóm tối đa 20 bản nháp hợp lệ có một lượt review bằng text model hiện tại (Codex dùng mức medium). Review trả quyết định theo từng mục: giữ nguyên, sửa kèm lý do, hoặc loại. Bộ kiểm tra yêu cầu đủ mục, không trùng chỉ số; dữ liệu review sai hoặc lỗi provider sẽ dừng batch trước khi tạo ảnh. Bản sửa phải qua kiểm tra schema và chống trùng lại; việc loại mục có thể phát sinh thêm lượt lập/duyệt để bù đủ số lượng. Lịch sử sự kiện hiển thị bước duyệt; context lưu thông tin quyết định để đối chiếu.

Review kiểm tra tính hợp lý, đồng nhất tiêu đề/nội dung, sự đa dạng của cả nhóm và khả năng ghép. Không có bước tự động tra cứu/xác minh nguồn bên ngoài: khi cần độ chính xác của mẫu đặc trưng mà chưa có căn cứ, chọn vật quen thuộc hoặc loại concept. Đây là kiểm tra văn bản, không bảo đảm ảnh sinh ra không có lỗi hình học. Giữ duyệt ảnh cuối và không tự tạo lại ảnh lỗi. Batch đã có context từ phiên bản trước không bị tự viết lại.

`art-direction.json` hiện chứa bộ tiêu chí ngắn dùng chung; lịch sử feedback chỉ để tham khảo, không được nối toàn bộ vào prompt. Bộ lọc 35 công thức vẫn hỗ trợ chống lặp, không thay thế review AI.

### Antigravity (thử nghiệm, hạn mức Google AI Pro)

Trong **Cài đặt → Nhà cung cấp**, chọn **Antigravity cục bộ**, rồi lưu. Chuyển lại **Codex cục bộ** để dùng Codex. Model context Antigravity được lưu riêng (`antigravity_text_model`); để trống dùng mặc định của `agy`. Model ảnh API chỉ áp dụng cho OpenAI API.

Cài [Antigravity CLI](https://www.antigravity.google/docs/cli/install/) và chạy `agy` để đăng nhập đúng tài khoản Google có gói Pro. Trong `/settings`, tắt **Use G1 Credits** nếu chỉ muốn dùng hạn mức gói. Adapter từ chối cấu hình bật `useG1Credits` hoặc provider API/ADC, loại biến API key khỏi tiến trình con, và không tự chuyển sang API tính phí. CLI lưu cấu hình thưa nên giá trị boolean `false` có thể bị lược bỏ khi ghi lại file. Có thể xem quota bằng `/usage` và credits bằng `/credits` trong CLI.

Adapter dùng [headless stream-json](https://www.antigravity.google/docs/cli/headless/), JSON schema cho bước lập/duyệt context, và công cụ tạo ảnh tích hợp. Nó không dùng SDK/API Gemini. Mỗi lượt ảnh có thư mục riêng. Ứng dụng lấy ảnh từ artifact của đúng conversation ID và tự sao chép về thư mục lượt chạy, giữ định dạng gốc; agent không cần chạy lệnh `cp`. Nếu CLI tạo bản chỉnh sửa, chọn đúng file cuối được báo trong kết quả, không chọn theo thời gian file. CLI có thể tự tinh chỉnh ảnh bên trong subagent, vì vậy một yêu cầu của ứng dụng có thể dùng nhiều hơn một lượt tạo ảnh. Lỗi, timeout hoặc không có file ảnh sẽ dừng lượt mà không tự gọi lại. CLI chạy ở chế độ accept-edits trong sandbox, không bật tự động chấp thuận mọi quyền CLI; nếu CLI từ chối công cụ, xem `process.log`/`events.jsonl` trong thư mục lượt chạy để cấu hình quyền phù hợp.

Nên thử **1 context, 1 ảnh, concurrency 1** trước khi chạy batch. Trạng thái CLI/model khả dụng không chứng minh quota hoặc quyền tạo ảnh còn hiệu lực. Kiểm tra thật ngày 2026-10-06: đã xác nhận lập context, duyệt context, tạo ảnh bằng công cụ tích hợp và xuất JPEG 600×900. Lỗi treo trước đó đến từ MCP GitKraken không kết nối xong. CLI 1.3.0 vẫn khởi động MCP này dù `agy mcp disable GitKraken` báo đã tắt; sao lưu rồi dùng `agy mcp remove GitKraken` đã giải quyết. Cấu hình trước khi gỡ được lưu tại `~/.gemini/config/mcp_config.before-photo-generator.json` trên máy này. Chỉ khôi phục khi đã sửa được MCP, nếu không headless có thể lại treo. Các unit tests dùng CLI giả; kiểm chứng thật lưu trong `data/antigravity-smoke/`.
