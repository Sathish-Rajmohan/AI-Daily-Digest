# Feed catalogue

Ready-made topics to paste into the `topics` array in
[topics.json](topics.json). Every feed URL returned entries when it was added.

[Broad topics](#broad-topics) cover a whole subject, like a section of a
newspaper. [Niche topics](#niche-topics) cover one interest within a subject.
You can use both, for example a `Sports` topic for general news and a
`Sports: Soccer` topic for the league you follow.

If your subject isn't here, you can [find feeds for your own
topic](#finding-feeds-for-your-own-topic).

## Broad topics

<details>
<summary><b>Sports</b> (7 feeds)</summary>

```json
{
  "name": "Sports",
  "max_stories": 7,
  "feeds": [
    "https://www.espn.com/espn/rss/news",
    "https://feeds.bbci.co.uk/sport/rss.xml",
    "https://www.theguardian.com/sport/rss",
    "https://www.skysports.com/rss/12040",
    "https://www.cbssports.com/rss/headlines/",
    "https://sports.yahoo.com/rss/",
    "https://www.abc.net.au/news/feed/45910/rss.xml"
  ]
}
```
</details>

<details>
<summary><b>Finance & Economics</b> (6 feeds)</summary>

```json
{
  "name": "Finance & Economics",
  "max_stories": 7,
  "feeds": [
    "https://www.economist.com/finance-and-economics/rss.xml",
    "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "https://www.investing.com/rss/news.rss",
    "https://seekingalpha.com/feed.xml",
    "https://www.federalreserve.gov/feeds/press_all.xml",
    "https://theconversation.com/au/business/articles.atom"
  ]
}
```
</details>

<details>
<summary><b>Climate & Energy</b> (8 feeds)</summary>

```json
{
  "name": "Climate & Energy",
  "max_stories": 7,
  "feeds": [
    "https://www.carbonbrief.org/feed/",
    "https://insideclimatenews.org/feed/",
    "https://www.theguardian.com/environment/climate-crisis/rss",
    "https://grist.org/feed/",
    "https://www.eia.gov/rss/todayinenergy.xml",
    "https://cleantechnica.com/feed/",
    "https://www.nature.com/nclimate.rss",
    "https://yaleclimateconnections.org/feed/"
  ]
}
```
</details>

<details>
<summary><b>Space & Astronomy</b> (6 feeds)</summary>

```json
{
  "name": "Space & Astronomy",
  "max_stories": 6,
  "feeds": [
    "https://www.nasa.gov/rss/dyn/breaking_news.rss",
    "https://spacenews.com/feed/",
    "https://www.universetoday.com/feed/",
    "https://spaceflightnow.com/feed/",
    "https://arstechnica.com/science/space/feed/",
    "https://www.esa.int/rssfeed/Our_Activities/Space_News",
    "https://phys.org/rss-feed/space-news/"
  ]
}
```
</details>

<details>
<summary><b>Cybersecurity</b> (7 feeds)</summary>

```json
{
  "name": "Cybersecurity",
  "max_stories": 7,
  "feeds": [
    "https://krebsonsecurity.com/feed/",
    "https://www.bleepingcomputer.com/feed/",
    "https://feeds.feedburner.com/TheHackersNews",
    "https://www.schneier.com/feed/atom/",
    "https://www.darkreading.com/rss.xml",
    "https://therecord.media/feed/",
    "https://www.cisa.gov/cybersecurity-advisories/all.xml"
  ]
}
```
</details>

<details>
<summary><b>Startups & Venture</b> (6 feeds)</summary>

```json
{
  "name": "Startups & Venture",
  "max_stories": 6,
  "feeds": [
    "https://techcrunch.com/feed/",
    "https://news.crunchbase.com/feed/",
    "https://sifted.eu/feed",
    "https://tech.eu/feed/",
    "https://www.eu-startups.com/feed/",
    "https://www.saastr.com/feed/"
  ]
}
```
</details>

<details>
<summary><b>US Politics</b> (10 feeds)</summary>

These come from across the political spectrum, so the digest doesn't only
reflect one side. Remove any you don't want.

```json
{
  "name": "US Politics",
  "max_stories": 8,
  "feeds": [
    "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",
    "https://feeds.washingtonpost.com/rss/politics",
    "https://feeds.npr.org/1014/rss.xml",
    "https://thehill.com/news/feed/",
    "https://www.realclearpolitics.com/index.xml",
    "https://www.propublica.org/feeds/propublica/main",
    "https://www.nationalreview.com/feed/",
    "https://reason.com/feed/",
    "https://thedispatch.com/feed/",
    "https://www.motherjones.com/politics/feed/"
  ]
}
```
</details>

<details>
<summary><b>Gaming</b> (7 feeds)</summary>

```json
{
  "name": "Gaming",
  "max_stories": 7,
  "feeds": [
    "https://www.eurogamer.net/feed",
    "https://www.polygon.com/rss/index.xml",
    "https://www.rockpapershotgun.com/feed",
    "https://kotaku.com/rss",
    "https://www.gamedeveloper.com/rss.xml",
    "https://www.ign.com/rss/articles/feed",
    "https://www.pcgamer.com/rss/"
  ]
}
```
</details>

<details>
<summary><b>Film & TV</b> (6 feeds)</summary>

```json
{
  "name": "Film & TV",
  "max_stories": 6,
  "feeds": [
    "https://variety.com/feed/",
    "https://www.hollywoodreporter.com/feed/",
    "https://deadline.com/feed/",
    "https://www.indiewire.com/feed/",
    "https://www.theguardian.com/film/rss",
    "https://www.rogerebert.com/feed"
  ]
}
```
</details>

<details>
<summary><b>Music</b> (6 feeds)</summary>

```json
{
  "name": "Music",
  "max_stories": 6,
  "feeds": [
    "https://pitchfork.com/feed/feed-news/rss",
    "https://www.rollingstone.com/music/feed/",
    "https://www.theguardian.com/music/rss",
    "https://www.billboard.com/feed/",
    "https://consequence.net/feed/",
    "https://thequietus.com/feed"
  ]
}
```
</details>

<details>
<summary><b>Books & Ideas</b> (8 feeds)</summary>

```json
{
  "name": "Books & Ideas",
  "max_stories": 6,
  "feeds": [
    "https://lithub.com/feed/",
    "https://aeon.co/feed.rss",
    "https://www.theguardian.com/books/rss",
    "https://feeds.npr.org/1032/rss.xml",
    "https://electricliterature.com/feed/",
    "https://www.theparisreview.org/blog/feed/",
    "https://fivebooks.com/feed/",
    "https://newrepublic.com/rss.xml"
  ]
}
```
</details>

<details>
<summary><b>Crypto & Web3</b> (4 feeds)</summary>

```json
{
  "name": "Crypto & Web3",
  "max_stories": 6,
  "feeds": [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://www.theblock.co/rss.xml"
  ]
}
```
</details>

<details>
<summary><b>Design & Architecture</b> (4 feeds)</summary>

```json
{
  "name": "Design & Architecture",
  "max_stories": 5,
  "feeds": [
    "https://www.dezeen.com/feed/",
    "https://www.archdaily.com/rss/",
    "https://www.core77.com/blog/rss.xml",
    "https://www.designboom.com/feed/"
  ]
}
```
</details>

<details>
<summary><b>Cars & EVs</b> (4 feeds)</summary>

```json
{
  "name": "Cars & EVs",
  "max_stories": 6,
  "feeds": [
    "https://electrek.co/feed/",
    "https://insideevs.com/rss/articles/all/",
    "https://www.caranddriver.com/rss/all.xml/",
    "https://jalopnik.com/rss"
  ]
}
```
</details>

<details>
<summary><b>Australia</b> (5 feeds)</summary>

```json
{
  "name": "Australia",
  "max_stories": 7,
  "feeds": [
    "https://www.abc.net.au/news/feed/51120/rss.xml",
    "https://www.theguardian.com/australia-news/rss",
    "https://www.smh.com.au/rss/national.xml",
    "https://theconversation.com/au/articles.atom",
    "https://www.crikey.com.au/feed/"
  ]
}
```
</details>

## Niche topics

Each of these covers one interest within a broader subject.

### Sports

<details>
<summary><b>Soccer</b>: European leagues, internationals, transfer news.</summary>

```json
{
  "name": "Sports: Soccer",
  "max_stories": 6,
  "feeds": [
    "https://www.espn.com/espn/rss/soccer/news",
    "https://www.theguardian.com/football/rss",
    "https://feeds.bbci.co.uk/sport/football/rss.xml",
    "https://www.skysports.com/rss/11095"
  ]
}
```
</details>

<details>
<summary><b>Basketball (NBA)</b>: NBA news, trades and analysis.</summary>

```json
{
  "name": "Sports: Basketball (NBA)",
  "max_stories": 6,
  "feeds": [
    "https://www.espn.com/espn/rss/nba/news",
    "https://www.cbssports.com/rss/headlines/nba/",
    "https://sports.yahoo.com/nba/rss/",
    "https://basketball.realgm.com/rss/wiretap/0/0.xml"
  ]
}
```
</details>

<details>
<summary><b>NFL</b>: NFL news, injuries, analytics.</summary>

```json
{
  "name": "Sports: NFL",
  "max_stories": 6,
  "feeds": [
    "https://www.espn.com/espn/rss/nfl/news",
    "https://www.cbssports.com/rss/headlines/nfl/",
    "https://profootballtalk.nbcsports.com/feed/",
    "https://www.pff.com/feed"
  ]
}
```
</details>

<details>
<summary><b>Cricket</b>: Tests, ODIs, T20 and domestic.</summary>

```json
{
  "name": "Sports: Cricket",
  "max_stories": 5,
  "feeds": [
    "https://www.espncricinfo.com/rss/content/story/feeds/0.xml",
    "https://feeds.bbci.co.uk/sport/cricket/rss.xml",
    "https://www.theguardian.com/sport/cricket/rss"
  ]
}
```
</details>

<details>
<summary><b>Motorsport (F1)</b>: Formula 1 racing, technical and paddock coverage.</summary>

```json
{
  "name": "Sports: Motorsport (F1)",
  "max_stories": 6,
  "feeds": [
    "https://www.motorsport.com/rss/f1/news/",
    "https://www.autosport.com/rss/f1/news/",
    "https://feeds.bbci.co.uk/sport/formula1/rss.xml",
    "https://www.racefans.net/feed/",
    "https://www.the-race.com/feed/"
  ]
}
```
</details>

<details>
<summary><b>Tennis</b>: Tours, majors and rankings.</summary>

```json
{
  "name": "Sports: Tennis",
  "max_stories": 5,
  "feeds": [
    "https://www.espn.com/espn/rss/tennis/news",
    "https://feeds.bbci.co.uk/sport/tennis/rss.xml",
    "https://www.theguardian.com/sport/tennis/rss"
  ]
}
```
</details>

### Tech

<details>
<summary><b>AI research</b>: Lab announcements and practitioner write-ups, not vendor marketing.</summary>

```json
{
  "name": "Tech: AI research",
  "max_stories": 6,
  "feeds": [
    "https://huggingface.co/blog/feed.xml",
    "https://openai.com/news/rss.xml",
    "https://deepmind.google/blog/rss.xml",
    "https://simonwillison.net/atom/everything/",
    "https://www.marktechpost.com/feed/"
  ]
}
```
</details>

<details>
<summary><b>Open source & dev</b>: Kernels, languages, toolchains and developer practice.</summary>

```json
{
  "name": "Tech: Open source & dev",
  "max_stories": 6,
  "feeds": [
    "https://github.blog/feed/",
    "https://stackoverflow.blog/feed/",
    "https://lwn.net/headlines/rss",
    "https://www.phoronix.com/rss.php",
    "https://dev.to/feed"
  ]
}
```
</details>

<details>
<summary><b>Chips & hardware</b>: Silicon, servers, and the semiconductor supply chain.</summary>

```json
{
  "name": "Tech: Chips & hardware",
  "max_stories": 5,
  "feeds": [
    "https://www.techpowerup.com/rss/news",
    "https://www.extremetech.com/feed",
    "https://semiengineering.com/feed/",
    "https://www.servethehome.com/feed/"
  ]
}
```
</details>

<details>
<summary><b>Consumer gadgets</b>: Phones, laptops and reviews.</summary>

```json
{
  "name": "Tech: Consumer gadgets",
  "max_stories": 6,
  "feeds": [
    "https://www.engadget.com/rss.xml",
    "https://www.theverge.com/rss/index.xml",
    "https://www.cnet.com/rss/news/",
    "https://gizmodo.com/rss",
    "https://www.androidauthority.com/feed/"
  ]
}
```
</details>

### Finance & Business

<details>
<summary><b>Personal finance</b>: Saving, investing and household money, written for individuals.</summary>

```json
{
  "name": "Finance: Personal finance",
  "max_stories": 5,
  "feeds": [
    "https://www.nerdwallet.com/blog/feed/",
    "https://ofdollarsanddata.com/feed/",
    "https://awealthofcommonsense.com/feed/"
  ]
}
```
</details>

<details>
<summary><b>Macro & central banks</b>: Rate decisions and official statements, straight from the source.</summary>

```json
{
  "name": "Finance: Macro & central banks",
  "max_stories": 5,
  "feeds": [
    "https://www.federalreserve.gov/feeds/press_all.xml",
    "https://www.ecb.europa.eu/rss/press.html",
    "https://feeds.content.dowjones.io/public/rss/mw_topstories"
  ]
}
```
</details>

<details>
<summary><b>Fintech & payments</b>: Payments, banking technology and regulation.</summary>

```json
{
  "name": "Finance: Fintech & payments",
  "max_stories": 5,
  "feeds": [
    "https://www.finextra.com/rss/headlines.aspx",
    "https://www.pymnts.com/feed/",
    "https://fintechnews.sg/feed/"
  ]
}
```
</details>

<details>
<summary><b>Real estate</b>: Housing markets, commercial property and construction.</summary>

```json
{
  "name": "Finance: Real estate",
  "max_stories": 6,
  "feeds": [
    "https://www.housingwire.com/feed/",
    "https://www.bisnow.com/rss",
    "https://www.multifamilydive.com/feeds/news/",
    "https://www.propertyupdate.com.au/feed/"
  ]
}
```
</details>

<details>
<summary><b>Energy</b>: Oil, gas, utilities and the grid.</summary>

```json
{
  "name": "Business: Energy",
  "max_stories": 6,
  "feeds": [
    "https://oilprice.com/rss/main",
    "https://www.utilitydive.com/feeds/news/",
    "https://www.eia.gov/rss/todayinenergy.xml",
    "https://www.rigzone.com/news/rss/rigzone_latest.aspx"
  ]
}
```
</details>

<details>
<summary><b>Aviation</b>: Airlines, aircraft and air traffic.</summary>

```json
{
  "name": "Business: Aviation",
  "max_stories": 5,
  "feeds": [
    "https://simpleflying.com/feed/",
    "https://www.flightglobal.com/rss",
    "https://www.aerotime.aero/feed"
  ]
}
```
</details>

<details>
<summary><b>Retail & consumer</b>: Retail, grocery and fashion trade.</summary>

```json
{
  "name": "Business: Retail & consumer",
  "max_stories": 6,
  "feeds": [
    "https://www.retaildive.com/feeds/news/",
    "https://www.modernretail.co/feed/",
    "https://www.grocerydive.com/feeds/news/",
    "https://www.businessoffashion.com/feed/",
    "https://www.retailgazette.co.uk/feed/"
  ]
}
```
</details>

<details>
<summary><b>Pharma</b>: Drug development, approvals and the industry.</summary>

```json
{
  "name": "Business: Pharma",
  "max_stories": 6,
  "feeds": [
    "https://www.fiercepharma.com/rss/xml",
    "https://www.pharmatimes.com/rss",
    "https://www.biopharmadive.com/feeds/news/",
    "https://www.statnews.com/category/pharma/feed/",
    "https://www.drugdiscoverytrends.com/feed/"
  ]
}
```
</details>

### Science & Health

<details>
<summary><b>Biotech & genomics</b>: Trials, approvals and the biotech business.</summary>

```json
{
  "name": "Science: Biotech & genomics",
  "max_stories": 6,
  "feeds": [
    "https://www.statnews.com/category/biotech/feed/",
    "https://www.fiercebiotech.com/rss/xml",
    "https://www.genengnews.com/feed/",
    "https://www.nature.com/nbt.rss"
  ]
}
```
</details>

<details>
<summary><b>Neuroscience & psychology</b>: Brain and behaviour research for a general reader.</summary>

```json
{
  "name": "Science: Neuroscience & psychology",
  "max_stories": 6,
  "feeds": [
    "https://www.thetransmitter.org/feed/",
    "https://www.sciencedaily.com/rss/mind_brain.xml",
    "https://bigthink.com/feed/",
    "https://nautil.us/feed/"
  ]
}
```
</details>

<details>
<summary><b>Physics & maths</b>: Physics, maths and the fundamental sciences.</summary>

```json
{
  "name": "Science: Physics & maths",
  "max_stories": 5,
  "feeds": [
    "https://phys.org/rss-feed/physics-news/",
    "https://physicsworld.com/feed/",
    "https://www.sciencedaily.com/rss/matter_energy.xml"
  ]
}
```
</details>

<details>
<summary><b>Public health & policy</b>: Outbreaks, health systems and policy.</summary>

```json
{
  "name": "Health: Public health & policy",
  "max_stories": 5,
  "feeds": [
    "https://kffhealthnews.org/feed/",
    "https://www.statnews.com/feed/",
    "https://www.medpagetoday.com/rss/headlines.xml"
  ]
}
```
</details>

<details>
<summary><b>Nutrition & fitness</b>: Food, exercise and the evidence behind them.</summary>

```json
{
  "name": "Health: Nutrition & fitness",
  "max_stories": 5,
  "feeds": [
    "https://www.outsideonline.com/feed/",
    "https://www.nytimes.com/svc/collections/v1/publish/https://www.nytimes.com/section/well/rss.xml"
  ]
}
```
</details>

### Gaming & Culture

<details>
<summary><b>Nintendo & console</b>: Platform-specific console coverage.</summary>

```json
{
  "name": "Gaming: Nintendo & console",
  "max_stories": 6,
  "feeds": [
    "https://www.nintendolife.com/feeds/latest",
    "https://www.pushsquare.com/feeds/latest",
    "https://www.purexbox.com/feeds/latest",
    "https://www.videogameschronicle.com/feed/"
  ]
}
```
</details>

<details>
<summary><b>Esports</b>: Competitive play, teams and tournaments.</summary>

```json
{
  "name": "Gaming: Esports",
  "max_stories": 5,
  "feeds": [
    "https://dotesports.com/feed",
    "https://www.hltv.org/rss/news",
    "https://esportsinsider.com/feed"
  ]
}
```
</details>

<details>
<summary><b>Indie & game dev</b>: Making games, and the games most outlets skip.</summary>

```json
{
  "name": "Gaming: Indie & game dev",
  "max_stories": 5,
  "feeds": [
    "https://www.gamedeveloper.com/rss.xml",
    "https://indiegamesplus.com/feed",
    "https://www.rockpapershotgun.com/feed"
  ]
}
```
</details>

<details>
<summary><b>Anime & manga</b>: Japanese animation, manga and licensing.</summary>

```json
{
  "name": "Culture: Anime & manga",
  "max_stories": 5,
  "feeds": [
    "https://www.animenewsnetwork.com/all/rss.xml",
    "https://otakumode.com/news/feed",
    "https://www.cbr.com/feed/"
  ]
}
```
</details>

<details>
<summary><b>Streaming & TV</b>: New shows, renewals and cancellations.</summary>

```json
{
  "name": "Culture: Streaming & TV",
  "max_stories": 5,
  "feeds": [
    "https://tvline.com/feed/",
    "https://decider.com/feed/",
    "https://www.avclub.com/rss"
  ]
}
```
</details>

<details>
<summary><b>Photography & visual</b>: Cameras, technique and photographic work.</summary>

```json
{
  "name": "Culture: Photography & visual",
  "max_stories": 5,
  "feeds": [
    "https://petapixel.com/feed/",
    "https://www.dpreview.com/feeds/news.xml",
    "https://fstoppers.com/feed"
  ]
}
```
</details>

### Regions

<details>
<summary><b>UK</b>: British national news.</summary>

```json
{
  "name": "Region: UK",
  "max_stories": 6,
  "feeds": [
    "https://feeds.bbci.co.uk/news/uk/rss.xml",
    "https://www.theguardian.com/uk-news/rss",
    "https://inews.co.uk/feed",
    "https://www.standard.co.uk/rss"
  ]
}
```
</details>

<details>
<summary><b>Europe</b>: Continental Europe and EU institutions.</summary>

```json
{
  "name": "Region: Europe",
  "max_stories": 6,
  "feeds": [
    "https://rss.dw.com/rdf/rss-en-world",
    "https://www.politico.eu/feed/",
    "https://www.france24.com/en/europe/rss",
    "https://www.euronews.com/rss?level=theme&name=news"
  ]
}
```
</details>

<details>
<summary><b>India</b>: Indian national news across the political spectrum.</summary>

```json
{
  "name": "Region: India",
  "max_stories": 5,
  "feeds": [
    "https://www.thehindu.com/news/national/feeder/default.rss",
    "https://indianexpress.com/feed/",
    "https://www.livemint.com/rss/news"
  ]
}
```
</details>

<details>
<summary><b>China & Hong Kong</b>: China coverage from inside and outside the mainland.</summary>

```json
{
  "name": "Region: China & Hong Kong",
  "max_stories": 5,
  "feeds": [
    "https://www.scmp.com/rss/91/feed",
    "https://www.sixthtone.com/rss",
    "https://chinadigitaltimes.net/feed/"
  ]
}
```
</details>

<details>
<summary><b>Middle East</b>: Regional coverage from several vantage points.</summary>

```json
{
  "name": "Region: Middle East",
  "max_stories": 6,
  "feeds": [
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://www.timesofisrael.com/feed/",
    "https://www.middleeasteye.net/rss",
    "https://www.al-monitor.com/rss"
  ]
}
```
</details>

<details>
<summary><b>Africa</b>: Pan-African and Nigerian national coverage.</summary>

```json
{
  "name": "Region: Africa",
  "max_stories": 5,
  "feeds": [
    "https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf",
    "https://www.theafricareport.com/feed/",
    "https://www.premiumtimesng.com/feed"
  ]
}
```
</details>

<details>
<summary><b>Latin America</b>: South American regional and national coverage.</summary>

```json
{
  "name": "Region: Latin America",
  "max_stories": 5,
  "feeds": [
    "https://en.mercopress.com/rss/",
    "https://www.batimes.com.ar/feed",
    "https://riotimesonline.com/feed/"
  ]
}
```
</details>

## Finding feeds for your own topic

Paste this into a chat assistant that can search the web, with your topic
filled in. It asks the assistant to open every URL it suggests. Assistants
often give feed URLs from memory, and many of those stopped working years ago.

```
I need RSS/Atom feed URLs for a daily news digest on: <YOUR TOPIC>.

Find 6-10 feeds and return them as a JSON array of URL strings, nothing else.

Requirements:
1. Verify each URL actually resolves right now and returns RSS or Atom XML.
   Do not give me a URL you have not checked. If you cannot check it, leave
   it out. Guessing /feed or /rss on a domain is not checking.
2. Each feed must have published something in the last 7 days. Say which
   ones you could not confirm.
3. Full-site or section feeds only. No search-query feeds, no per-author or
   per-tag feeds, no Google News or other aggregator-generated feeds, no
   Reddit, no YouTube.
4. The feed must carry a headline and a text summary or description per
   item. Title-only feeds are no use to me.
5. Prefer publications with an editorial masthead. Avoid content farms, SEO
   blogs and press-release wires.
6. Spread them across different outlets and countries. Where the topic is
   contested, include outlets with different editorial positions.
7. No feed behind a login or a hard paywall that strips the summary text.

For each one, tell me in a sentence: the outlet, roughly how often it
publishes, and its angle or specialism.
```

### Checking them yourself

To check a list of feeds at once:

```bash
python - <<'EOF'
import feedparser
for url in [
    "https://example.com/feed",
]:
    d = feedparser.parse(url)
    print(len(d.entries), url)
EOF
```

A feed that prints `0` has no entries, even if the page opens in a browser.

After adding feeds, run the workflow by hand and read the log. If a topic
shows `found 0 raw articles`, none of its feeds returned anything recent.

### Feeds without dates

An item with no publish date is always treated as new, because there's nothing
to compare with the lookback window. A feed like that can send the same items
every day. If you see repeats from one feed, remove it.

## Known dead

These were checked and didn't work:

AP, Reuters (all sections), Politico.com (the EU edition works), Axios,
FiveThirtyEight, Vox, a16z, VentureBeat, PitchBook, Bleacher Report, The
Athletic, Sports Illustrated, Fox Sports, NBA.com, HoopsHype, SLAM,
Cricbuzz, Tennis.com, Football365, IMF, VoxEU, Brookings, World Bank, OECD,
BIS, Bank of England, Investopedia, The Real Deal, Inman, NYBooks, New
Yorker section feeds, AFR, Sky & Telescope, PortSwigger, AIGA Eye on
Design, Autoblog, City Journal, AnandTech (site closed), Crunchyroll,
Vulture, Scroll.in, The Wire (India), Caixin, Al Arabiya, Mail & Guardian,
Nation Africa, Euractiv, CIDRAP, Endpoints News, Pharmaphorum, Examine,
Chain Store Age, RetailWire, Spectrum News, APA Monitor, BPS Digest,
GameFromScratch, Esports Charts, LensCulture, Berkeley BAIR.

These work, but post too rarely to turn up in a 24-hour lookback:
International Crisis Group, CMU ML blog, Rust blog, Quanta section feeds,
WHO news, Mr Money Mustache, Calculated Risk, SemiAnalysis, Aviation Week,
Telegraph, The China Project, Accenture Banking, Precision Nutrition,
Psychology Today, Neuroscience News, RBA media releases.
