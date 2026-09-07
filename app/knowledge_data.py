"""
Editable content for the knowledge base — product catalog, policies, FAQ,
About Us.

Edit THIS file to add/change products, prices, policies, or FAQ answers.
No changes to prompts.py, tools.py, or the pipeline are ever needed for a
content update — that's the whole point of pulling this out of the prompt.

Keep entries short and speech-friendly (they get read aloud by TTS): plain
sentences, no markdown, no bullet symbols inside the text itself.
"""

ABOUT_US = (
    "Vedonica International Pvt. Ltd. combines ancient Ayurvedic wisdom with "
    "modern science to create safe, effective, and easy-to-use wellness "
    "products. We believe in holistic health and use carefully sourced herbs "
    "with uncompromised quality. Our mission is to bring natural balance and "
    "better health into everyday life. Official website is vedonica.in. "
    "Customers can also order from Flipkart or Amazon."
)

# `aliases` = extra words customers might say that should also match this
# product (nicknames, short forms, common mispronunciations transliterated).
PRODUCTS = [
    {
        "id": "collagen_effervescent",
        "name": "Vedonica Collagen Effervescent Tablets",
        "aliases": ["collagen", "collagen tablets", "effervescent", "skin tablets"],
        "price": "₹799",
        "benefits": [
            "supports skin hydration and elasticity",
            "supports healthy hair and nails",
            "supports joint flexibility",
        ],
        "ingredients": [
            "Biotin",
            "Vitamin C",
            "Ayurvedic collagen-support ingredients",
        ],
        "other": "Vegan, Ayurvedic, currently in stock, free shipping, COD available",
    },
    {
        "id": "fat_cutter_ras",
        "name": "Vedonica Vedo Fat Cutter Ras",
        "aliases": ["fat cutter", "vedo ras", "weight loss ras", "fat cutter juice"],
        "price": "₹399",
        "benefits": [
            "supports weight management",
            "supports metabolism",
            "supports digestion",
            "supports energy levels",
        ],
        "ingredients": [],
        "other": "500ml bottle, strawberry flavour",
    },
]

# Free-form policy documents — shipping, returns, privacy, T&Cs, whatever
# your business needs. Keys are just internal ids (used for the entry id);
# the "topic" name spoken/matched comes from tokenizing the key + text.
POLICIES = {
    "shipping": (
        "Vedonica offers free shipping on all orders across India. Orders "
        "are usually dispatched within 1 to 2 business days and delivered "
        "within 5 to 7 business days depending on location. Cash on "
        "delivery, COD, is available."
    ),
    "returns_refund": (
        "Products can be returned within 7 days of delivery if they are "
        "unopened and in original packaging. Refunds are processed within "
        "5 to 7 business days after the returned item is received and "
        "inspected."
    ),
    "privacy": (
        "Vedonica collects only the information needed to process orders "
        "and provide support, such as name, phone number, address, and "
        "order details. This information is not sold to third parties."
    ),
    "terms_and_conditions": (
        "By ordering from Vedonica, customers agree to provide accurate "
        "delivery details and use products as directed on the label. "
        "Vedonica products are wellness supplements and are not a "
        "substitute for medical treatment."
    ),
}

# Simple Q/A pairs. Add as many as you want — search cost is still just a
# fast in-memory keyword match, not another prompt token cost.
FAQS = [
    {
        "question": "Is COD available?",
        "answer": "Haan, Cash on Delivery available hai sabhi orders par.",
    },
    {
        "question": "How long does delivery take?",
        "answer": "Delivery mein aam taur par 5 se 7 business days lagte hain.",
    },
    {
        "question": "Can I return a product?",
        "answer": (
            "Haan, agar product unopened hai aur original packaging mein "
            "hai, toh delivery ke 7 din ke andar return kar sakte hain."
        ),
    },
    {
        "question": "Are Vedonica products safe?",
        "answer": (
            "Vedonica products Ayurvedic ingredients se bante hain aur "
            "carefully sourced hote hain, lekin kisi bhi health condition "
            "ke liye pehle doctor se salah lena behtar hai."
        ),
    },
    {
        "question": "Where can I buy Vedonica products?",
        "answer": "Aap vedonica.in, Amazon, ya Flipkart se order kar sakte hain.",
    },
]
