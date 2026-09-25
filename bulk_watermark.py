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

def process_watermark(base_img, template_img):
    TW, TH = template_img.size
    
    # 1. ปรับขนาดรูปต้นฉบับให้พอดีกับขนาดเทมเพลต (1920x1280) โดยอัตโนมัติ
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


class SharkWatermarkApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Bulk Watermark Pro - Shark Box")
        self.root.geometry("850x640")
        self.root.minsize(800, 580)

        self.input_dir = tk.StringVar()
        self.template_path = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.scan_subfolders = tk.BooleanVar(value=False)

        self.preview_photo = None
        self.build_ui()

    def build_ui(self):
        main = ttk.Frame(self.root, padding=16)
        main.pack(fill="both", expand=True)

        ttk.Label(main, text="Shark Box - Bulk Watermark", font=("Arial", 20, "bold")).pack(anchor="w")
        ttk.Label(main, text="ระบบใส่ลายน้ำอัจฉริยะ: แถบสีแดงและ QR Code ทึบ 100% คมชัด + ลายน้ำกลางภาพโปร่งแสง").pack(anchor="w", pady=(0, 12))

        # 1. Folder รูป
        box1 = ttk.LabelFrame(main, text="1. โฟลเดอร์รูปต้นฉบับ", padding=10)
        box1.pack(fill="x", pady=5)
        row1 = ttk.Frame(box1)
        row1.pack(fill="x")
        ttk.Entry(row1, textvariable=self.input_dir).pack(side="left", fill="x", expand=True)
        ttk.Button(row1, text="เลือกโฟลเดอร์", command=self.choose_input).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(box1, text="ค้นหารูปในโฟลเดอร์ย่อยด้วย (Subfolders)", variable=self.scan_subfolders, command=self.make_preview).pack(anchor="w", pady=(4, 0))

        # 2. Template
        box2 = ttk.LabelFrame(main, text="2. ไฟล์เทมเพลต (ใช้ไฟล์รูป JPG หรือ PNG ของร้านได้เลย)", padding=10)
        box2.pack(fill="x", pady=5)
        row2 = ttk.Frame(box2)
        row2.pack(fill="x")
        ttk.Entry(row2, textvariable=self.template_path).pack(side="left", fill="x", expand=True)
        ttk.Button(row2, text="เลือกเทมเพลต", command=self.choose_template).pack(side="left", padx=(8, 0))

        # 3. Output
        box3 = ttk.LabelFrame(main, text="3. โฟลเดอร์ผลลัพธ์", padding=10)
        box3.pack(fill="x", pady=5)
        row3 = ttk.Frame(box3)
        row3.pack(fill="x")
        ttk.Entry(row3, textvariable=self.output_dir).pack(side="left", fill="x", expand=True)
        ttk.Button(row3, text="เลือกโฟลเดอร์", command=self.choose_output).pack(side="left", padx=(8, 0))

        # Preview & Action
        bottom = ttk.Frame(main)
        bottom.pack(fill="both", expand=True, pady=(8, 0))

        preview_box = ttk.LabelFrame(bottom, text="Preview ตัวอย่างผลลัพธ์", padding=6)
        preview_box.pack(side="left", fill="both", expand=True)
        self.preview_label = ttk.Label(preview_box, text="เลือกโฟลเดอร์รูปและเทมเพลตเพื่อดู Preview")
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
        return img_list

    def make_preview(self):
        images = self.get_images()
        self.file_count_label.configure(text=f"พบรูป: {len(images)} รูป")
        t_path = self.template_path.get()
        if not images or not os.path.isfile(t_path):
            return
        try:
            base_img = Image.open(images[0])
            temp_img = Image.open(t_path)
            res = process_watermark(base_img, temp_img)
            res.thumbnail((480, 340), Image.Resampling.LANCZOS)
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
        threading.Thread(target=self.generate, args=(images,), daemon=True).start()

    def generate(self, images):
        try:
            template = Image.open(self.template_path.get())
            total = len(images)
            for i, path in enumerate(images, 1):
                base_img = Image.open(path)
                result = process_watermark(base_img, template)

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