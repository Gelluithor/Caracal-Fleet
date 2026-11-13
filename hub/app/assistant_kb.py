"""What the assistant knows: short, checked notes on the common tasks of Fleet, in Czech and English (Slovak, German
and Polish users get the English ones, the model translates). The assistant picks the notes that fit the question
and the page and gives only those to the model: a small prompt is what lets a small model answer quickly on any
server, and the notes keep it from inventing. Without a model the best note is the answer."""
import os
import re
import unicodedata

# id: (keywords, {lang: (title, text)}); keywords in both languages, without diacritics, word stems are enough
NOTES = {
    'add_device': ('pridat pridani nova novou obrazovk zarizeni instal sd karta usb raspberry add new screen device install card',
        {'cs': ('Přidání obrazovky', 'Klikněte vpravo nahoře na **Přidat zařízení** a vyberte způsob: SD karta pro Raspberry Pi (zero-touch), USB disk pro PC, jeden příkaz na běžícím Linuxu, nebo instalace přes SSH. Zařízení se samo nainstaluje a objeví se v [Zařízení](#/devices) po 10–30 minutách.'),
         'en': ('Adding a screen', 'Click **Add device** at the top right and choose how: an SD card for a Raspberry Pi (zero-touch), a USB stick for a PC, one command on a running Linux, or installation over SSH. The device installs itself and appears in [Devices](#/devices) after 10–30 minutes.')}),
    'offline': ('offline nefunguje nejde neodpovida spojeni sit vypadek mrtv nedostupn offline down not working unreachable network connection lost',
        {'cs': ('Zařízení je offline', '1. Zkontrolujte napájení a že televize i Raspberry Pi svítí.\n2. Zkontrolujte síť: kabel nebo Wi-Fi, případně restartujte router.\n3. Agent se k Fleetu připojuje sám ven: firewall musí povolit spojení na adresu Fleetu.\n4. Když zařízení naběhne, objeví se znovu jako online; historii výpadků najdete na jeho stránce. Problémy všech zařízení jsou ve [Vyžaduje pozornost](#/attention).'),
         'en': ('A device is offline', '1. Check the power: the TV and the Raspberry Pi are on.\n2. Check the network: cable or Wi-Fi, restart the router if needed.\n3. The agent connects out to Fleet itself: a firewall must allow connections to the address of Fleet.\n4. When the device is back it shows as online again; its outages are on its page. Problems of all devices are in [Needs attention](#/attention).')}),
    'black': ('cerna cerny obrazovka nic neukazuje prehravac nebezi zaseknut black screen nothing shows player not running stuck frozen',
        {'cs': ('Obrazovka je černá nebo se zasekla', 'Na stránce zařízení zkuste v akcích **Obnovit stránku**, pak **Restart playeru**, a když nepomůže, v **Správa** restart zařízení. Záložka Přehled zařízení ukazuje, co se právě přehrává; **Diagnostika** v menu Správa vypíše disk, paměť, displej a log.'),
         'en': ('The screen is black or stuck', 'On the device page try **Reload page** in its actions, then **Restart player**, and if that does not help, reboot it under **Manage**. The device overview shows what plays now; **Diagnostics** under Manage lists the disk, memory, display and log.')}),
    'many_screens': ('stejny obsah vic vice obrazovk vsechny hromadne globalni nasadit same content many screens all global deploy bulk',
        {'cs': ('Stejný obsah na víc obrazovek', 'Otevřete [Globální playlisty](#/global), vytvořte playlist, přidejte do něj obsah a klikněte na **Nasadit**. Vyberete obrazovky, skupiny nebo lokality a Fleet ukáže, co se na každé změní. Každá změna playlistu se ukládá jako verze, takže jde vrátit.'),
         'en': ('The same content on many screens', 'Open [Global playlists](#/global), create a playlist, add the content and click **Deploy**. Pick screens, groups or locations; Fleet shows what changes on each. Every change of the playlist is kept as a version you can restore.')}),
    'content': ('obsah pridat web stranku obrazek video widget kamera dokument pdf playlist add content website picture image video widget camera document',
        {'cs': ('Obsah jedné obrazovky', 'Otevřete zařízení, záložku **Playlist** a tlačítko **Přidat**: web, více na jedné obrazovce, widget (hodiny, počasí, zprávy, kalendář…), obrázek, video, kamera nebo dokument. Pořadí změníte přetažením, další akce položky jsou v menu **…**.'),
         'en': ('Content of one screen', 'Open the device, its **Playlist** tab and **Add**: a website, several on one screen, a widget (clock, weather, news, calendar…), a picture, a video, a camera or a document. Drag to reorder; more actions of an item are in its **…** menu.')}),
    'multi': ('vic vice na jedne obrazovce rozdelit ctyri dva weby mrizka vedle sebe several one screen split four two grid side by side',
        {'cs': ('Víc věcí na jedné obrazovce', 'V playlistu zařízení nebo v globálním playlistu dejte **Přidat → Více na jedné obrazovce**, vyberte rozvržení (dvě, tři, čtyři, nebo velká a dvě malé) a do každého pole web nebo widget. Na Raspberry Pi s 1 GB paměti dávejte přednost dvěma polím.'),
         'en': ('Several things on one screen', 'In the playlist of a device or a global playlist use **Add → Several on one screen**, pick a layout (two, three, four, or one large and two small) and put a website or a widget in each cell. On a Raspberry Pi with 1 GB of memory prefer two cells.')}),
    'scroll': ('posunout odscrollovat scroll zacatek stranky dole posun scrolled start page down position',
        {'cs': ('Stránka posunutá dolů', 'V playlistu zařízení otevřete u webu menu **…** a **Začátek stránky…**. Položka se ukáže na televizi, tlačítky ji posunete a dáte **Uložit tuto pozici**. V globálním playlistu jde zadat počet pixelů.'),
         'en': ('A page that starts scrolled down', 'In the playlist of a device open the **…** menu of the website and **Where the page starts…**. The item shows on the TV, the buttons scroll it, then **Save this position**. In a global playlist enter the pixels.')}),
    'schedule': ('plan rozvrh cas vecer noc noci vypnout vypinani vypnuti zapnout zapinani hodiny planovat schedule time night off on hours plan',
        {'cs': ('Plán a vypínání obrazovek', 'V [Plán](#/schedule) nastavíte hodiny obrazovek (v noci vypnuté přes HDMI-CEC), playlisty podle denní doby a dne v týdnu a akce v daný čas (restart, zapnutí, oznámení). Co kdy hraje, ukazuje [Co kdy hraje](#/timeline).'),
         'en': ('Schedules and screens off', 'In [Schedule](#/schedule) set screen hours (off at night through HDMI-CEC), playlists by time of day and weekday, and actions at a time (restart, on, a notification). [What plays when](#/timeline) shows the result.')}),
    'emergency': ('nouzove hlaseni evakuace pozar vsechny obrazovky hned emergency evacuation fire all screens now alert message takeover',
        {'cs': ('Nouzové hlášení', 'V [Nouzové hlášení](#/takeover) napíšete text, vyberete všechny obrazovky nebo lokality a spustíte. Zpráva je hned přes celou obrazovku, dokud ji neukončíte nebo nevyprší; pak playlisty pokračují.'),
         'en': ('Emergency message', 'In [Emergency message](#/takeover) write the text, pick all screens or locations and start it. It covers the screens at once until you end it or it runs out; then the playlists go on.')}),
    'notify': ('oznameni upozorneni hlidac zabbix jira grafana na obrazovce notifikace notification watcher alert on screen popup',
        {'cs': ('Oznámení na obrazovkách', 'Na stránce zařízení v menu **Oznámení** pošlete oznámení nebo nastavíte vzhled. Hlídače se samy ptají jiných aplikací (Jira, GitLab, Zabbix…) na nové položky. Jiné aplikace posílají oznámení přes API s tokenem z [Nastavení](#/settings).'),
         'en': ('Notifications on screens', 'On a device page, the **Notifications** menu sends one or sets their look. Watchers ask other apps (Jira, GitLab, Zabbix…) for new items. Other apps send notifications through the API with a token from [Settings](#/settings).')}),
    'update': ('aktualizace aktualizovat nova verze agent caracal update upgrade version agent',
        {'cs': ('Aktualizace', 'CARACAL na obrazovkách aktualizujete v [Aktualizace CARACALu](#/updates) (i postupně po skupinách), agenta v akcích zařízení **Aktualizovat agenta**, samotný Fleet v [Nastavení](#/settings) → Aktualizace Fleetu.'),
         'en': ('Updates', 'Update CARACAL on the screens in [CARACAL updates](#/updates) (also staged), the agent with **Update agent** in the device actions, and Fleet itself in [Settings](#/settings) → Fleet update.')}),
    'users': ('uzivatel uzivatele role prava pristup kolega pozvat users roles permissions access invite colleague',
        {'cs': ('Uživatelé a práva', 'V [Uživatelé](#/users) přidáte uživatele a dáte mu roli (prohlížející, operátor, manažer, admin) pro všechna zařízení nebo jen některé lokality a skupiny.'),
         'en': ('Users and permissions', 'In [Users](#/users) add a user and give them a role (viewer, operator, manager, admin) for all devices or only some locations and groups.')}),
    'backup': ('zaloha zalohy obnova migrace server presun backup restore migration move server',
        {'cs': ('Zálohy', 'V [Nastavení](#/settings) → Záloha a migrace stáhnete zálohu nebo nastavíte kopie mimo server (S3, NAS, SFTP, cloud). Na novém serveru zálohu obnovíte a zařízení se připojí samy.'),
         'en': ('Backups', 'In [Settings](#/settings) → Backup and migration download a backup or set copies outside the server (S3, NAS, SFTP, cloud). Restore it on a new server and the devices reconnect by themselves.')}),
}
TEXTS = {'cs': 'cs', 'sk': 'cs'}   # Slovak reads Czech notes better than English ones


def _fold(text):
    text = unicodedata.normalize('NFKD', str(text).lower())
    return ''.join(c for c in text if not unicodedata.combining(c))


def _same(word, key):
    """How much of the word the key covers (0 to 1). The same stem counts: Czech endings vary (černá, černou), so a
    shared beginning of 4 letters is enough; a short key inside a longer word (off, offline) counts less."""
    n = len(os.path.commonprefix([word, key]))
    return n / max(len(word), len(key)) if n >= 4 or (n == len(key) and n >= 3) else 0


KEYS = {nid: set(keys.split()) for nid, (keys, _) in NOTES.items()}
# a word that is in many notes (screen, device) says little: rarer words weigh more
DF = {k: sum(k in ks for ks in KEYS.values()) for ks in KEYS.values() for k in ks}


def pick(question, context=None, limit=2):
    """The notes that fit the question best (word stems against keywords, rarer words weigh more), plus the one the
    page suggests; a second note only when it fits almost as well as the first."""
    words = set(re.findall(r'[a-z0-9]{3,}', _fold(question)))
    scores = {}
    for nid, keys in KEYS.items():
        scores[nid] = sum(max((_same(w, k) / DF[k] for k in keys), default=0) for w in words)
    ctx = context or {}
    dev = ctx.get('device') if isinstance(ctx.get('device'), dict) else None
    # "what is wrong with it?" on the page of an offline device: the offline note comes first
    vague = words & {'proc', 'zarizeni', 'zarizenim', 'tim', 'tento', 'toto', 'why', 'what', 'device', 'this', 'wrong'}
    if dev and dev.get('online') is False and vague:
        scores['offline'] += 1
    ranked = sorted(((s, n) for n, s in scores.items() if s > 0), reverse=True)
    return [n for s, n in ranked[:limit] if s >= ranked[0][0] * .6]


def note(nid, lang):
    texts = NOTES[nid][1]
    return texts.get(TEXTS.get(lang, lang)) or texts['en']
