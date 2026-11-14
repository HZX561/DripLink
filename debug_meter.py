
from pathlib import Path
import cv2

from watermeter.pipeline import load_selection, load_detector

image_path = Path("my_photos/water1.jpg")
image = cv2.imread(str(image_path))

config = load_selection()
detector = load_detector(config["detector"])

result = detector.predict(
    image,
    device="cpu",
    imgsz=512,
    conf=0.05,
    iou=0.5,
    verbose=False
)[0]

names = detector.names
annotated = image.copy()

print("===== 检测框位置 =====")

if result.boxes is not None:
    for box in result.boxes:
        cls = int(box.cls.item())
        conf = float(box.conf.item())
        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()

        print(
            f"{names[cls]}: "
            f"置信度={conf:.4f}, "
            f"位置=({x1:.1f},{y1:.1f},"
            f"{x2:.1f},{y2:.1f})"
        )

        cv2.rectangle(
            annotated,
            (int(x1), int(y1)),
            (int(x2), int(y2)),
            (0, 255, 0),
            2
        )

        cv2.putText(
            annotated,
            f"{names[cls]} {conf:.2f}",
            (int(x1), max(20, int(y1)-8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 0),
            2
        )

output_path = Path("reports/debug_result.jpg")
output_path.parent.mkdir(parents=True, exist_ok=True)

cv2.imwrite(str(output_path), annotated)

print("\n图片已保存到：", output_path)
print("诊断完成！")
