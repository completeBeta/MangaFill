"""manga-image-translator — vendored geometry (GPL-3.0).

Upstream : https://github.com/zyddnys/manga-image-translator
Commit   : 95227a2bb0fd306cd4f0c104d57284026f991b3a (2026-07-20, main)
Licence  : GNU General Public License v3.0 — full text at /LICENSES/GPL-3.0.txt
           (canonical: https://www.gnu.org/licenses/gpl-3.0.txt)

Manga Fill as a whole is distributed under the GNU Affero General Public License
v3.0. AGPL-3.0 section 13 gives permission to "link or combine any covered work
with a work licensed under version 3 of the GNU General Public License into a
single combined work"; the files in THIS package remain governed by GPL-3.0, keep
their own notices, and must stay under GPL-3.0 if they are ever separated from
this project.

What is here and why: `ballon_extractor` derives a speech balloon's interior for
ONE text block from a local window on the ORIGINAL page. Our own region resolver
worked page-wide, so two balloons whose outlines touch (or whose outline our
inpaint erased) came back as a single interior and English was fitted across both
outlines — 1,199 of 2,606 lettered blocks in the whole-library audit. The local
window is what makes the two come apart.
"""
