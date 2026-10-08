import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import numpy as np
from PIL import Image, ImageTk, ImageOps

# รองรับ HEIC ของ iPhone (ถ้ามีไลบรารี)
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF_SUPPORTED = True
except ImportError:
    HEIF_SUPPORTED = False

SUPPORTED = [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"]
if HEIF_SUPPORTED:
    SUPPORTED.append(".heic")
SUPPORTED = tuple(SUPPORTED)

def detect_template_type(template_img):
    TW, TH = template_img.size
    temp_arr = np.array(template_img.convert("RGB"), dtype=np.float32)

    # เช็คว่าเป็นเทมเพลตเดิม (Shark Box) หรือไม่: มีแถบสีแดงกว้าง > 20% ของภาพที่ช่วงล่าง
    search_y_start = int(TH * 0.8)
    is_red = (temp_arr[search_y_start:, :, 0] > temp_arr[search_y_start:, :, 1] + 30) & \
             (temp_arr[search_y_start:, :, 0] > temp_arr[search_y_start:, :, 2] + 30)
    red_rows = np.where(np.sum(is_red, axis=1) > TW * 0.2)[0]

    if len(red_rows) > 0:
        return "shark"
    return "vantera"

def process_shark_watermark(base_img, template_img):
    """ลอจิกเดิม: สำหรับเทมเพลต Shark Box (แถบแดงล่าง + QR Code ขอบเขียว LINE)"""
    TW, TH = template_img.size
    
    # 1. ปรับขนาดรูปต้นฉบับให้พอดีกับขนาดเทมเพลต
    base_img = ImageOps.exif_transpose(base_img)
    base_fitted = ImageOps.fit(base_img, (TW, TH), Image.Resampling.LANCZOS).convert("RGB")
    base_arr = np.array(base_fitted, dtype=np.float32)
    temp_arr = np.array(template_img.convert("RGB"), dtype=np.float32)
    
    result_arr = base_arr.copy()
    
    # 2. ค้นหาแถบสีแดงด้านล่าง (ช่วง 20% ล่างของภาพ)
    search_y_start = int(TH * 0.8)
    is_red = (temp_arr[search_y_start:, :, 0] > temp_arr[search_y_start:, :, 1] + 30) & \
             (temp_arr[search_y_start:, :, 0] > temp_arr[search_y_start:, :, 2] + 30)
    red_rows = np.where(np.sum(is_red, axis=1) > TW * 0.2)[0]
    banner_y = (search_y_start + red_rows[0]) if len(red_rows) > 0 else int(TH * 0.92)
        
    # 3. ค้นหากรอบคิวอาร์โค้ด LINE (เม็ดสีเขียว)
    is_green = (temp_arr[:, :, 1] > temp_arr[:, :, 0] + 30) & \
               (temp_arr[:, :, 1] > temp_arr[:, :, 2] + 30) & \
               (temp_arr[:, :, 1] > 120)
    coords_green = np.argwhere(is_green)
    qr_mask = np.zeros((TH, TW), dtype=bool)
    if len(coords_green) > 0:
        gy_min, gy_max = coords_green[:, 0].min(), coords_green[:, 0].max()
        gx_min, gx_max = coords_green[:, 1].min(), coords_green[:, 1].max()
        pad = int(TH * 0.006)
        qy1, qy2 = max(0, gy_min - pad), min(TH, gy_max + pad)
        qx1, qx2 = max(0, gx_min - pad), min(TW, gx_max + pad)
        qr_mask[qy1:qy2, qx1:qx2] = True
        
    # 4. โลโก้มุมบนขวา (โซน 25% ขวาบน)
    logo_y_end = int(TH * 0.25)
    logo_x_start = int(TW * 0.75)
    tr_crop = temp_arr[:logo_y_end, logo_x_start:]
    is_tr_logo = np.min(tr_crop, axis=2) < 235
    
    logo_mask = np.zeros((TH, TW), dtype=bool)
    logo_mask[:logo_y_end, logo_x_start:] = is_tr_logo
    
    # 5. ใส่ลายน้ำจางๆ ตรงกลาง (Blend เฉพาะพื้นที่รูปที่ไม่ใช่แถบและโลโก้)
    exempt_mask = np.zeros((TH, TW), dtype=bool)
    exempt_mask[banner_y:, :] = True
    exempt_mask |= qr_mask
    exempt_mask |= logo_mask
    
    for c in range(3):
        result_arr[:, :, c] = np.where(
            exempt_mask,
            result_arr[:, :, c],
            result_arr[:, :, c] * temp_arr[:, :, c] / 255.0
        )
        
    # 6. วางโลโก้มุมบนขวาแบบคมชัด พร้อมไดคัทขอบขาวออกให้โปร่งใส
    tr_bg = result_arr[:logo_y_end, logo_x_start:]
    alpha = 1.0 - np.min(tr_crop, axis=2) / 255.0
    alpha = np.clip((alpha - 0.05) / 0.90, 0.0, 1.0)
    alpha = np.clip(alpha * 2.5, 0.0, 1.0)
    alpha_3d = np.expand_dims(alpha, axis=2)
    result_arr[:logo_y_end, logo_x_start:] = tr_crop * alpha_3d + tr_bg * (1.0 - alpha_3d)
    
    # 7. วางแถบสีแดงและ QR Code แบบทึบ 100% คมชัด สีสด
    result_arr[banner_y:, :] = temp_arr[banner_y:, :]
    if len(coords_green) > 0:
        result_arr[qr_mask] = temp_arr[qr_mask]
        
    return Image.fromarray(result_arr.astype(np.uint8))


def process_vantera_watermark(base_img, template_img):
    """ลอจิกใหม่: สำหรับเทมเพลต VANTERA (แถบเขียวล่าง + QR Code ดำขาวมุมขวาล่าง)"""
    TW, TH = template_img.size
    
    # 1. ปรับขนาดรูปต้นฉบับให้พอดีกับขนาดเทมเพลต
    base_img = ImageOps.exif_transpose(base_img)
    base_fitted = ImageOps.fit(base_img, (TW, TH), Image.Resampling.LANCZOS).convert("RGB")
    base_arr = np.array(base_fitted, dtype=np.float32)
    temp_arr = np.array(template_img.convert("RGB"), dtype=np.float32)
    
    result_arr = base_arr.copy()

    # 2. สแกนหากรอบ QR Code ที่มุมขวาล่าง (ช่วง Y 70%-92% และ X > 60%)
    upper_qr = (temp_arr[int(TH * 0.70):int(TH * 0.92), int(TW * 0.60):] < 245).any(axis=2)
    uy, ux = np.where(upper_qr)
    if len(uy) > 0:
        qr_y1 = int(TH * 0.70) + int(uy.min())
        qr_x1 = int(TW * 0.60) + int(ux.min())
    else:
        qr_y1 = int(TH * 0.758)
        qr_x1 = int(TW * 0.838)

    # 3. สแกนหาแถบสีด้านล่าง (ฝั่งซ้ายของกรอบ QR Code ในช่วง 15% ล่าง)
    left_bot = temp_arr[int(TH * 0.85):, :qr_x1]
    banner_rows = np.where(np.sum((left_bot < 245).any(axis=2), axis=1) > qr_x1 * 0.4)[0]
    if len(banner_rows) > 0:
        banner_y1 = int(TH * 0.85) + int(banner_rows[0])
    else:
        banner_y1 = int(TH * 0.929)

    # 4. โลโก้มุมบนขวา (โซน 25% ขวาบน)
    logo_y_end = int(TH * 0.25)
    logo_x_start = int(TW * 0.75)
    tr_crop = temp_arr[:logo_y_end, logo_x_start:]
    is_tr_logo = np.min(tr_crop, axis=2) < 235

    # 5. มาร์กพื้นที่ยกเว้น เพื่อให้ Blend เฉพาะลายน้ำจางๆ ตรงกลาง
    exempt_mask = np.zeros((TH, TW), dtype=bool)
    exempt_mask[banner_y1:, :qr_x1] = True
    exempt_mask[qr_y1:, qr_x1:] = True
    exempt_mask[:logo_y_end, logo_x_start:] |= is_tr_logo

    for c in range(3):
        result_arr[:, :, c] = np.where(
            exempt_mask,
            result_arr[:, :, c],
            result_arr[:, :, c] * temp_arr[:, :, c] / 255.0
        )

    # 6. วางโลโก้มุมบนขวาแบบคมชัด พร้อมไดคัทขอบขาวออกให้โปร่งใส
    tr_bg = result_arr[:logo_y_end, logo_x_start:]
    alpha = 1.0 - np.min(tr_crop, axis=2) / 255.0
    alpha = np.clip((alpha - 0.05) / 0.90, 0.0, 1.0)
    alpha = np.clip(alpha * 2.5, 0.0, 1.0)
    alpha_3d = np.expand_dims(alpha, axis=2)
    result_arr[:logo_y_end, logo_x_start:] = tr_crop * alpha_3d + tr_bg * (1.0 - alpha_3d)

    # 7. วางแถบด้านล่างและกรอบ QR Code แบบทึบ 100% คมชัด สีสด ชัดเจน
    result_arr[banner_y1:, :qr_x1] = temp_arr[banner_y1:, :qr_x1]
    result_arr[qr_y1:, qr_x1:] = temp_arr[qr_y1:, qr_x1:]

    return Image.fromarray(result_arr.astype(np.uint8))


def process_watermark(base_img, template_img, mode="auto"):
    """รองรับทั้ง Shark Box (เดิม) และ VANTERA (ใหม่) โดยตรวจจับอัตโนมัติหรือตามที่เลือก"""
    if mode == "auto":
        mode = detect_template_type(template_img)
        
    if mode == "shark":
        return process_shark_watermark(base_img, template_img)
    else:
        return process_vantera_watermark(base_img, template_img)


class SharkWatermarkApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Bulk Watermark Pro - Shark Box & VANTERA")
        self.root.geometry("870x680")
        self.root.minsize(820, 620)

        self.input_dir = tk.StringVar()
        self.template_path = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.template_mode = tk.StringVar(value="auto")
        self.scan_subfolders = tk.BooleanVar(value=False)

        # ค่าเริ่มต้นอัตโนมัติถ้ามีไฟล์ในโฟลเดอร์ปัจจุบัน
        if os.path.isdir("input"):
            self.input_dir.set(os.path.abspath("input"))
        if os.path.isfile("VANTERA.png"):
            self.template_path.set(os.path.abspath("VANTERA.png"))
        if os.path.isdir("output"):
            self.output_dir.set(os.path.abspath("output"))

        self.preview_photo = None
        self.build_ui()
        if self.input_dir.get() and self.template_path.get():
            self.root.after(100, self.make_preview)

    def build_ui(self):
        main = ttk.Frame(self.root, padding=16)
        main.pack(fill="both", expand=True)

        ttk.Label(main, text="Bulk Watermark Pro", font=("Arial", 20, "bold")).pack(anchor="w")
        ttk.Label(main, text="ระบบใส่ลายน้ำอัจฉริยะ: รองรับทั้ง Shark Box (แถบแดง) และ VANTERA (แถบเขียว/QR ขวาล่าง)").pack(anchor="w", pady=(0, 10))

        # 1. Folder รูป
        box1 = ttk.LabelFrame(main, text="1. โฟลเดอร์รูปต้นฉบับ", padding=10)
        box1.pack(fill="x", pady=4)
        row1 = ttk.Frame(box1)
        row1.pack(fill="x")
        ttk.Entry(row1, textvariable=self.input_dir).pack(side="left", fill="x", expand=True)
        ttk.Button(row1, text="เลือกโฟลเดอร์", command=self.choose_input).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(box1, text="ค้นหารูปในโฟลเดอร์ย่อยด้วย (Subfolders)", variable=self.scan_subfolders, command=self.make_preview).pack(anchor="w", pady=(4, 0))

        # 2. Template
        box2 = ttk.LabelFrame(main, text="2. ไฟล์เทมเพลตลายน้ำ (VANTERA / Shark Box)", padding=10)
        box2.pack(fill="x", pady=4)
        row2 = ttk.Frame(box2)
        row2.pack(fill="x")
        ttk.Entry(row2, textvariable=self.template_path).pack(side="left", fill="x", expand=True)
        ttk.Button(row2, text="เลือกเทมเพลต", command=self.choose_template).pack(side="left", padx=(8, 0))

        # แถวเลือกโหมดเทมเพลต
        mode_row = ttk.Frame(box2)
        mode_row.pack(fill="x", pady=(6, 0))
        ttk.Label(mode_row, text="รูปแบบเทมเพลต:").pack(side="left", padx=(0, 6))
        mode_cb = ttk.Combobox(mode_row, textvariable=self.template_mode, state="readonly", width=38)
        mode_cb["values"] = (
            "auto: ตรวจจับอัตโนมัติ (Auto Detect)",
            "vantera: VANTERA (แถบเขียวล่าง + QR Code ดำขาวมุมขวาล่าง)",
            "shark: Shark Box (แถบแดงล่าง + QR Code เขียว LINE)",
        )
        mode_cb.current(0)
        mode_cb.pack(side="left")
        mode_cb.bind("<<ComboboxSelected>>", lambda e: self.make_preview())

        self.detected_label = ttk.Label(mode_row, text="", foreground="#007acc", font=("Arial", 9, "bold"))
        self.detected_label.pack(side="left", padx=(10, 0))

        # 3. Output
        box3 = ttk.LabelFrame(main, text="3. โฟลเดอร์ผลลัพธ์", padding=10)
        box3.pack(fill="x", pady=4)
        row3 = ttk.Frame(box3)
        row3.pack(fill="x")
        ttk.Entry(row3, textvariable=self.output_dir).pack(side="left", fill="x", expand=True)
        ttk.Button(row3, text="เลือกโฟลเดอร์", command=self.choose_output).pack(side="left", padx=(8, 0))

        # Preview & Action
        bottom = ttk.Frame(main)
        bottom.pack(fill="both", expand=True, pady=(8, 0))

        preview_box = ttk.LabelFrame(bottom, text="Preview ตัวอย่างผลลัพธ์", padding=6)
        preview_box.pack(side="left", fill="both", expand=True)
        self.preview_label = ttk.Label(preview_box, text="เลือกโฟลเดอร์รูปและเทมเพลตเพื่อดู Preview", anchor="center")
        self.preview_label.pack(fill="both", expand=True)

        ctrl_box = ttk.LabelFrame(bottom, text="การทำงาน", padding=12)
        ctrl_box.pack(side="right", fill="y", padx=(10, 0))

        self.file_count_label = ttk.Label(ctrl_box, text="พบรูป: 0 รูป", font=("Arial", 10, "bold"), foreground="blue")
        self.file_count_label.pack(pady=4)

        self.status = ttk.Label(ctrl_box, text="พร้อมทำงาน", wraplength=170)
        self.status.pack(pady=8)

        self.progress = ttk.Progressbar(ctrl_box, length=170, mode="determinate")
        self.progress.pack(pady=8)

        self.generate_btn = ttk.Button(ctrl_box, text="Generate ทั้งหมด", command=self.start_generate)
        self.generate_btn.pack(fill="x", pady=10)
        ttk.Button(ctrl_box, text="รีเฟรช Preview", command=self.make_preview).pack(fill="x")

    def get_selected_mode(self):
        val = self.template_mode.get()
        if "vantera" in val:
            return "vantera"
        elif "shark" in val:
            return "shark"
        return "auto"

    def choose_input(self):
        d = filedialog.askdirectory(title="เลือกโฟลเดอร์รูปต้นฉบับ")
        if d:
            self.input_dir.set(d)
            if not self.output_dir.get():
                self.output_dir.set(os.path.join(d, "watermarked_output"))
            self.make_preview()

    def choose_template(self):
        p = filedialog.askopenfilename(title="เลือกไฟล์เทมเพลต", filetypes=[("Image files", "*.jpg *.jpeg *.png *.webp")])
        if p:
            self.template_path.set(p)
            self.make_preview()

    def choose_output(self):
        d = filedialog.askdirectory(title="เลือกโฟลเดอร์บันทึกผลลัพธ์")
        if d:
            self.output_dir.set(d)

    def get_images(self):
        d = self.input_dir.get()
        if not os.path.isdir(d):
            return []
        img_list = []
        if self.scan_subfolders.get():
            for root, _, files in os.walk(d):
                for f in files:
                    if f.lower().endswith(SUPPORTED) and not f.startswith("."):
                        img_list.append(os.path.join(root, f))
        else:
            for f in os.listdir(d):
                p = os.path.join(d, f)
                if os.path.isfile(p) and f.lower().endswith(SUPPORTED) and not f.startswith("."):
                    img_list.append(p)
        return sorted(img_list)

    def make_preview(self):
        images = self.get_images()
        self.file_count_label.configure(text=f"พบรูป: {len(images)} รูป")
        t_path = self.template_path.get()
        if not images or not os.path.isfile(t_path):
            return
        try:
            base_img = Image.open(images[0])
            temp_img = Image.open(t_path)
            mode = self.get_selected_mode()
            if mode == "auto":
                detected = detect_template_type(temp_img)
                self.detected_label.configure(text=f"ตรวจพบ: {'Shark Box' if detected == 'shark' else 'VANTERA'}")
            else:
                self.detected_label.configure(text=f"ใช้งาน: {'Shark Box' if mode == 'shark' else 'VANTERA'}")

            res = process_watermark(base_img, temp_img, mode=mode)
            res.thumbnail((500, 360), Image.Resampling.LANCZOS)
            self.preview_photo = ImageTk.PhotoImage(res)
            self.preview_label.configure(image=self.preview_photo, text="")
        except Exception as e:
            self.preview_label.configure(image="", text=f"Preview error: {e}")

    def start_generate(self):
        if not os.path.isdir(self.input_dir.get()):
            messagebox.showwarning("ข้อมูลไม่ครบ", "กรุณาเลือกโฟลเดอร์รูปต้นฉบับ")
            return
        if not os.path.isfile(self.template_path.get()):
            messagebox.showwarning("ข้อมูลไม่ครบ", "กรุณาเลือกไฟล์เทมเพลต")
            return
        images = self.get_images()
        if not images:
            messagebox.showwarning("ไม่พบรูป", "ไม่พบไฟล์รูปที่รองรับในโฟลเดอร์")
            return

        os.makedirs(self.output_dir.get(), exist_ok=True)
        self.generate_btn.configure(state="disabled")
        mode = self.get_selected_mode()
        threading.Thread(target=self.generate, args=(images, mode), daemon=True).start()

    def generate(self, images, mode):
        try:
            template = Image.open(self.template_path.get())
            total = len(images)
            for i, path in enumerate(images, 1):
                base_img = Image.open(path)
                result = process_watermark(base_img, template, mode=mode)

                rel_path = os.path.relpath(path, self.input_dir.get())
                out_path = os.path.join(self.output_dir.get(), rel_path)
                os.makedirs(os.path.dirname(out_path), exist_ok=True)

                out_file = os.path.splitext(out_path)[0] + ".jpg"
                result.save(out_file, "JPEG", quality=95)

                self.root.after(0, self.update_progress, i, total, os.path.basename(path))
            self.root.after(0, self.done)
        except Exception as e:
            self.root.after(0, self.failed, str(e))

    def update_progress(self, i, total, name):
        self.progress["maximum"] = total
        self.progress["value"] = i
        self.status.configure(text=f"กำลังทำ ({i}/{total}):\n{name}")

    def done(self):
        self.generate_btn.configure(state="normal")
        self.status.configure(text="เสร็จสิ้นทั้งหมดแล้ว!")
        messagebox.showinfo("สำเร็จ", "ใส่ลายน้ำและกรอบให้รูปทั้งหมดเรียบร้อยแล้วครับ")

    def failed(self, error):
        self.generate_btn.configure(state="normal")
        self.status.configure(text="เกิดข้อผิดพลาด")
        messagebox.showerror("Error", error)

if __name__ == "__main__":
    root = tk.Tk()
    app = SharkWatermarkApp(root)
    root.mainloop()