"""Realistic posts in the style of Batumi rental chats, for parser checks."""

SAMPLES = [
    ("""Сдается 2+1 квартира в Orbi City, 65 кв.м, 12 этаж из 24.
Вид на море, с мебелью и техникой. Долгосрочно 600$ в месяц.
Депозит 600$. Без комиссии, от собственника. @leo_batumi +995 555 12 34 56""",
     dict(deal_type="rent_offer", term="long", price=600.0, currency="USD",
          rooms=3, bedrooms=2, layout="2+1", area_sqm=65.0, floor=12,
          floors_total=24, complex_name="Orbi City", sea_view=1, is_agent=0)),

    ("""Сдам студию посуточно, Новый бульвар, 35 м2, 5 этаж. 70 лари в сутки.
Свободна с 15 октября. Тел: 577112233""",
     dict(deal_type="rent_offer", term="daily", price=70.0, currency="GEL",
          rooms=1, bedrooms=0, layout="studio", area_sqm=35.0, floor=5,
          district="Новый бульвар")),

    ("""Ищу квартиру 1+1 в районе Химшиашвили на длительный срок,
бюджет до 400$. Без животных.""",
     dict(deal_type="rent_seek", term="long", rooms=2, bedrooms=1,
          layout="1+1", district="Химшиашвили", price=400.0, currency="USD")),

    ("""Продается трехкомнатная квартира 88 кв м, Горгасали, 95000$""",
     dict(deal_type="sale", rooms=3, area_sqm=88.0, district="Горгасали",
          price=95000.0, currency="USD")),

    ("""For rent: 1 bedroom apartment, Alliance Palace, 48 sqm, 9th floor.
Long term, price 550 usd per month. Sea view, furnished, parking.""",
     dict(deal_type="rent_offer", term="long", price=550.0, currency="USD",
          rooms=2, bedrooms=1, area_sqm=48.0, floor=9,
          complex_name="Alliance Palace", sea_view=1, furnished=1, parking=1)),

    ("""сдаётся однокомнатная квартира, ул. Пушкина, 40кв.м, 3/7 эт,
цена 1200 лари, можно с животными, есть мебель""",
     dict(deal_type="rent_offer", price=1200.0, currency="GEL", rooms=1,
          area_sqm=40.0, floor=3, floors_total=7, district="Пушкина",
          pets=1, furnished=1)),

    ("""Сдаю 3+1 в Gonio, первая линия, 120 кв.м. 800-1000$ в зависимости от сезона.
Агентство, комиссия 50%""",
     dict(deal_type="rent_offer", price=800.0, currency="USD", rooms=4,
          bedrooms=3, area_sqm=120.0, district="Гонио", is_agent=1)),

    ("""Всем привет! Подскажите, где можно поесть хачапури?""",
     dict(deal_type="other")),

    ("""СДАМ КВАРТИРУ 2+1 БАГРАТИОНИ 500 У.Е. БЕЗ МЕБЕЛИ 45 КВ.М 8 ЭТАЖ""",
     dict(deal_type="rent_offer", price=500.0, currency="USD", rooms=3,
          bedrooms=2, area_sqm=45.0, floor=8, district="Багратиони", furnished=0)),

    ("""Сдается квартира. Цена 450. Район Мелашвили, 2 комнатная, 55 кв.м.
Долгосрочно. Пишите в личку @agent_batumi""",
     dict(deal_type="rent_offer", term="long", price=450.0, currency=None,
          rooms=2, area_sqm=55.0, district="Мелашвили", contact="@agent_batumi")),

    # --- formats taken from the live chat ---------------------------------
    # Verbless block: no 'сдается' anywhere, the structure is the whole ad.
    ('⭕️ Згвиспирис 10 л \n\n      2+1 / 75 кв \n\n      12 этаж \n\n‼️Обе спальни с окнами \n\n‼️Посудомойка \n\n‼️Вид на море \n\n📌 1100 $',
     {'deal_type': 'rent_offer', 'price': 1100.0, 'currency': 'USD', 'rooms': 3, 'bedrooms': 2, 'layout': '2+1', 'area_sqm': 75.0, 'floor': 12, 'sea_view': 1, 'furnished': 1, 'address': 'Згвиспирис 10 л'}),

    # Template used by one of the busiest posters; pets shown as ❌🐕🐈.
    ('#Сдам\nD\nКвартира 2+1\n#1100$💵 \n📍Улица Згвиспири дом 10L\n✔️ балкон\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0 \n✔️Площадь 70\n✔️Стиральная машина\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0\xa0 \n✔️Кондиционер\xa0\xa0\xa0 \n✔️Отопление\n✔️Микроволновка\n✔️Этаж 12\n✔️Духовка\n✔️Посудамойка\nЦена 1100$ \n❌️🐕🐈\n☎️Тел:+995555000111\nℹ️Комиссия с клиента не взымается!!!\nℹ️Заселение по договору!!!',
     {'deal_type': 'rent_offer', 'price': 1100.0, 'currency': 'USD', 'rooms': 3, 'bedrooms': 2, 'layout': '2+1', 'area_sqm': 70.0, 'floor': 12, 'pets': 0, 'furnished': 1, 'phone': '+995555000111', 'is_agent': 0}),

    # Labelled-field format.
    ('📍 Адрес: Шартава 18 ( Calligraphy towers ) \n🛋️ Количество комнат: 1+1\n💰 Стоимость: 800$ \n\n• Центральное отопление  \n• Духовка , микроволновка , посудомойка\n• Просторный балкон с видом на стадион 🏟️ \n\n📲 Просмотр: @batumi_rent_demo\n\nВ канале можете найти другие эксклюзивные варианты 🕊️',
     {'deal_type': 'rent_offer', 'price': 800.0, 'currency': 'USD', 'rooms': 2, 'bedrooms': 1, 'layout': '1+1', 'address': 'Шартава 18', 'complex_name': 'Calligraphy', 'furnished': 1}),

    # Surcharges and a trailing deposit must not outrank the headline price.
    ('#сдаётся 1+1 Дом ГОРИЗОНТ\n📍 Шериф химшиашвили 49\n▫Площадь 50м²/Этаж 21\n▫Сдается на год\n✖️ без животных\n💵 500$\nЛетом +100$\n▫️Оплата первый последний месяц + 100$ возврашаемый депозит',
     {'deal_type': 'rent_offer', 'price': 500.0, 'currency': 'USD', 'rooms': 2, 'bedrooms': 1, 'layout': '1+1', 'area_sqm': 50.0, 'floor': 21, 'pets': 0}),

    # Struck-through old price: the second figure is the real one.
    ('🌈 ORBI BEACH TOWER | 2+1 | ВИД НА МОРЕ\nБольшой балкон, микроволновка, посуда\nНЕ КОММЕРЧЕСКИЙ ТАРИФ\n1400$ 1200$ на год\nПросмотр: @some_agent',
     {'deal_type': 'rent_offer', 'term': 'long', 'price': 1200.0, 'currency': 'USD', 'rooms': 3, 'bedrooms': 2, 'layout': '2+1', 'sea_view': 1, 'complex_name': 'Orbi Beach Tower'}),
]
