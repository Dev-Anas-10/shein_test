# SHEIN Cart Site

موقع Flask + gunicorn على بورت 80 (بدون venv) مع PostgreSQL.
بتحط رابط سلة SHEIN مشتركة (مباشر `landing` أو قصير `onelink`)، بيستخرج المنتجات بـ Playwright، وتضيفها لعربتك المحفوظة في الداتابيس.

## محتويات الريبو

```
shein-site/
├── app.py                    # تطبيق Flask
├── scraper.py                # الاستخراج (Playwright)
├── schema.sql                # جداول الداتابيس (آمن للتكرار)
├── templates/                # base / auth / order / result / cart
├── deploy/shein-site.service # ملف خدمة systemd
├── install.sh                # تثبيت كامل أول مرة
├── update.sh                 # تحديث من GitHub + إعادة تشغيل
├── .gitignore
└── README.md
```

## المتطلبات

- سيرفر Ubuntu 22.04 / 24.04 (أو Debian) وصلاحية root.
- بورت **80** (و22 للـ SSH) مفتوحين في Security Group (Inbound TCP 80 من Anywhere).

---

## 1) رفع الملفات على GitHub (مرة واحدة)

1. اعمل ريبو جديد على GitHub (مثلًا `shein-site`).
2. فك الـ zip على جهازك وادخل المجلد:

```bash
cd shein-site
git init
git add .
git commit -m "first version"
git branch -M main
git remote add origin https://github.com/USER/shein-site.git
git push -u origin main
```

> لو الريبو **Private** هتحتاج Personal Access Token (GitHub ← Settings ← Developer settings ← Tokens) بصلاحية `repo`، وتستخدمه كباسورد وقت الـ push.

## 2) التثبيت على السيرفر

```bash
ssh ubuntu@IP-السيرفر
sudo -i
apt-get update && apt-get install -y git
git clone https://github.com/USER/shein-site.git /tmp/shein-install
bash /tmp/shein-install/install.sh https://github.com/USER/shein-site.git
```

لو الريبو Private، حط التوكن في الرابط (في الأمرين):

```
https://TOKEN@github.com/USER/shein-site.git
```

السكريبت بيعمل كل حاجة لوحده:

1. يثبت الحزم (PostgreSQL, gunicorn, Flask, psycopg2, requests, Playwright + Chromium).
2. يسحب الكود من GitHub إلى `/opt/shein-site`.
3. يعمل `/etc/shein-site.env` (باسورد الداتابيس و SECRET_KEY عشوائيين) لو مش موجود.
4. يعمل مستخدم وداتابيس PostgreSQL ويطبق `schema.sql`.
5. يركّب خدمة `shein-site` ويشغلها على بورت 80.
6. يختبر الموقع ويطبعلك الرابط `http://IP`.

بعدها: سجّل حساب جديد ← "طلب أوردر" ← الصق الرابط ← "أضف للعربة".

> تشغيل `install.sh` تاني آمن: مبيمسحش الداتابيس ولا بيغير `SECRET_KEY` ولا باسورد الداتابيس.

## 3) التحديثات

1. عدّل الكود على جهازك وارفعه:

```bash
git add . && git commit -m "تعديل" && git push
```

2. على السيرفر:

```bash
sudo bash /opt/shein-site/update.sh
```

السكريبت بينزّل آخر كود من GitHub، يفحص إنه سليم (لو فيه خطأ بيرجع للنسخة القديمة)، يطبق `schema.sql` (جداول جديدة بس بدون مسح بيانات)، يحدّث ملف الخدمة، ويعمل restart.

خيارات:

| الأمر | الفايدة |
|---|---|
| `update.sh` | تحديث عادي من نفس الـ branch |
| `update.sh dev` | تحديث من branch تاني |
| `update.sh --force` | إعادة الضبط والتشغيل حتى لو مفيش تغييرات |
| `update.sh --deps` | يحدّث كمان حزم النظام و Playwright/Chromium |

> لو غيّرت جداول، ضيف التعديل في `schema.sql` بشكل آمن (`CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`) لأنه بيتشغل في كل تحديث.
> متعدلش الملفات مباشرة على السيرفر: التحديث بيستبدلها بنسخة GitHub.

## أوامر الصيانة

```bash
systemctl status shein-site --no-pager        # حالة الخدمة
journalctl -u shein-site -n 50 --no-pager     # اللوج
systemctl restart shein-site                  # إعادة تشغيل
cat /etc/shein-site.env                       # الإعدادات (سرّية)

# المستخدمين
PGPASSWORD=$(grep -oP '(?<=sheinuser:)[^@]+' /etc/shein-site.env) \
  psql -h 127.0.0.1 -U sheinuser -d sheindb -c "select id,email from users;"

# اختبار الاستخراج لوحده
cd /opt/shein-site
python3 scraper.py 'https://onelink.shein.com/57/65uen3gcoyfb?shc=2_RNi1fSZ9Ya4' | python3 -m json.tool | head -40
```

## حل المشاكل

| المشكلة | الحل |
|---|---|
| الموقع مش بيفتح من برّه | افتح بورت 80 في Security Group |
| `Address already in use` على 80 | في خدمة تانية على 80 (nginx/apache): `ss -ltnp \| grep :80` وأوقفها |
| الاستخراج بيفشل | جرّب `scraper.py` لوحده. SHEIN ممكن تطلب تحقق أو تحجب IP بتاع AWS |
| `Executable doesn't exist` (Playwright) | `python3 -m playwright install --with-deps chromium` أو `update.sh --deps` |
| التحديث رجّع النسخة القديمة | فيه خطأ في الكود الجديد — صلحه وارفعه وجرب تاني |
| `git fetch` بيطلب باسورد | الريبو Private: استخدم رابط فيه التوكن (`git -C /opt/shein-site remote set-url origin https://TOKEN@github.com/USER/shein-site.git`) |

## ملاحظات

- نسبة الخصم بتظهر بس لما `original_price` أكبر من `sale_price`.
- العملة ثابتة `SAR` في `scraper.py`؛ لو السوق اختلف لازم تتقرأ من الاستجابة.
- الباسوردات متشفرة (hash)، لكن الموقع **بدون HTTPS وبدون CSRF**: مناسب للاستخدام الشخصي. لو هتفتحه لناس كتير ضيف دومين + HTTPS (Nginx + Let's Encrypt) وCSRF.
