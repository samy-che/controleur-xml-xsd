"""Tests du moteur de correction.

    python3 -m unittest discover -s tests -v

Les cas couvrent surtout les constructions XSD ou un reordonnancement naif
casserait le document : xs:all, groupes repetes, choix illimites, extensions.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xsdfix.corrector import Options
from xsdfix.service import (
    STATUS_FAILED,
    STATUS_FIXED,
    STATUS_PARTIAL,
    STATUS_VALID,
    InputFile,
    analyze,
)

HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'xmlns="urn:t" targetNamespace="urn:t" elementFormDefault="qualified">')


def run(xsd, xml, options=None, extra_xsd=None):
    files = [InputFile("main.xsd", xsd.encode("utf-8"))]
    for name, content in (extra_xsd or {}).items():
        files.append(InputFile(name, content.encode("utf-8")))
    report = analyze(files, [InputFile("doc.xml", xml.encode("utf-8"))],
                     options or Options())
    assert report.schema_error is None, report.schema_error
    return report.results[0]


def order_of(result, tag_of_interest=None):
    """Noms des balises du XML corrige, dans l'ordre, au premier niveau."""
    from lxml import etree
    root = etree.fromstring(result.corrected)
    return [etree.QName(c).localname for c in root if isinstance(c.tag, str)]


class TestOrdering(unittest.TestCase):

    def test_sequence_simple(self):
        xsd = HEAD + '''
          <xs:element name="R"><xs:complexType><xs:sequence>
            <xs:element name="A" type="xs:string"/>
            <xs:element name="B" type="xs:string"/>
            <xs:element name="C" type="xs:string"/>
          </xs:sequence></xs:complexType></xs:element>
        </xs:schema>'''
        xml = '<R xmlns="urn:t"><C>3</C><A>1</A><B>2</B></R>'
        result = run(xsd, xml)
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertEqual(order_of(result), ["A", "B", "C"])

    def test_deja_conforme_non_modifie(self):
        xsd = HEAD + '''
          <xs:element name="R"><xs:complexType><xs:sequence>
            <xs:element name="A" type="xs:string"/>
            <xs:element name="B" type="xs:string"/>
          </xs:sequence></xs:complexType></xs:element>
        </xs:schema>'''
        result = run(xsd, '<R xmlns="urn:t"><A>1</A><B>2</B></R>')
        self.assertEqual(result.status, STATUS_VALID)
        self.assertEqual(result.changes, [])
        self.assertIsNone(result.corrected)

    def test_xs_all_ordre_libre_non_touche(self):
        """xs:all accepte n'importe quel ordre : rien ne doit bouger."""
        xsd = HEAD + '''
          <xs:element name="R"><xs:complexType><xs:all>
            <xs:element name="A" type="xs:string"/>
            <xs:element name="B" type="xs:string"/>
          </xs:all></xs:complexType></xs:element>
        </xs:schema>'''
        result = run(xsd, '<R xmlns="urn:t"><B>2</B><A>1</A></R>')
        self.assertEqual(result.status, STATUS_VALID)

    def test_groupe_repete_non_melange(self):
        """(A,B)* : le correcteur ne doit pas regrouper les A puis les B."""
        xsd = HEAD + '''
          <xs:element name="R"><xs:complexType>
            <xs:sequence maxOccurs="unbounded">
              <xs:element name="A" type="xs:string"/>
              <xs:element name="B" type="xs:string"/>
            </xs:sequence>
          </xs:complexType></xs:element>
        </xs:schema>'''
        xml = '<R xmlns="urn:t"><A>1</A><B>1</B><A>2</A><B>2</B></R>'
        result = run(xsd, xml)
        self.assertEqual(result.status, STATUS_VALID)

    def test_groupe_repete_precede_dun_element(self):
        """Z doit remonter devant le groupe repete, sans casser l'alternance."""
        xsd = HEAD + '''
          <xs:element name="R"><xs:complexType><xs:sequence>
            <xs:element name="Z" type="xs:string"/>
            <xs:sequence maxOccurs="unbounded">
              <xs:element name="A" type="xs:string"/>
              <xs:element name="B" type="xs:string"/>
            </xs:sequence>
          </xs:sequence></xs:complexType></xs:element>
        </xs:schema>'''
        xml = '<R xmlns="urn:t"><A>1</A><B>1</B><A>2</A><B>2</B><Z>z</Z></R>'
        result = run(xsd, xml)
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertEqual(order_of(result), ["Z", "A", "B", "A", "B"])

    def test_choice_illimite_ordre_preserve(self):
        """<xs:choice maxOccurs="unbounded"> autorise tout ordre : ne rien bouger."""
        xsd = HEAD + '''
          <xs:element name="R"><xs:complexType>
            <xs:choice maxOccurs="unbounded">
              <xs:element name="A" type="xs:string"/>
              <xs:element name="B" type="xs:string"/>
            </xs:choice>
          </xs:complexType></xs:element>
        </xs:schema>'''
        result = run(xsd, '<R xmlns="urn:t"><B>1</B><A>2</A><B>3</B></R>')
        self.assertEqual(result.status, STATUS_VALID)

    def test_extension_base_puis_extension(self):
        """Un type derive place d'abord le contenu de base, puis le sien."""
        xsd = HEAD + '''
          <xs:complexType name="Base"><xs:sequence>
            <xs:element name="A" type="xs:string"/>
            <xs:element name="B" type="xs:string"/>
          </xs:sequence></xs:complexType>
          <xs:complexType name="Derive">
            <xs:complexContent><xs:extension base="Base"><xs:sequence>
              <xs:element name="C" type="xs:string"/>
            </xs:sequence></xs:extension></xs:complexContent>
          </xs:complexType>
          <xs:element name="R" type="Derive"/>
        </xs:schema>'''
        result = run(xsd, '<R xmlns="urn:t"><C>3</C><B>2</B><A>1</A></R>')
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertEqual(order_of(result), ["A", "B", "C"])

    def test_groupe_nomme(self):
        xsd = HEAD + '''
          <xs:group name="G"><xs:sequence>
            <xs:element name="A" type="xs:string"/>
            <xs:element name="B" type="xs:string"/>
          </xs:sequence></xs:group>
          <xs:element name="R"><xs:complexType><xs:sequence>
            <xs:group ref="G"/>
            <xs:element name="C" type="xs:string"/>
          </xs:sequence></xs:complexType></xs:element>
        </xs:schema>'''
        result = run(xsd, '<R xmlns="urn:t"><C>3</C><B>2</B><A>1</A></R>')
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertEqual(order_of(result), ["A", "B", "C"])

    def test_groupe_de_substitution(self):
        xsd = HEAD + '''
          <xs:element name="Paiement" type="xs:string" abstract="true"/>
          <xs:element name="Virement" type="xs:string" substitutionGroup="Paiement"/>
          <xs:element name="R"><xs:complexType><xs:sequence>
            <xs:element name="A" type="xs:string"/>
            <xs:element ref="Paiement"/>
            <xs:element name="Z" type="xs:string"/>
          </xs:sequence></xs:complexType></xs:element>
        </xs:schema>'''
        xml = '<R xmlns="urn:t"><Z>z</Z><Virement>v</Virement><A>a</A></R>'
        result = run(xsd, xml)
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertEqual(order_of(result), ["A", "Virement", "Z"])

    def test_elements_locaux_non_qualifies(self):
        xsd = ('<?xml version="1.0"?>'
               '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
               'xmlns="urn:t" targetNamespace="urn:t" elementFormDefault="unqualified">'
               '<xs:element name="R"><xs:complexType><xs:sequence>'
               '<xs:element name="A" type="xs:string"/>'
               '<xs:element name="B" type="xs:string"/>'
               '</xs:sequence></xs:complexType></xs:element></xs:schema>')
        # en "unqualified", seule la racine porte l'espace de noms
        result = run(xsd, '<t:R xmlns:t="urn:t"><B>2</B><A>1</A></t:R>')
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertEqual(order_of(result), ["A", "B"])

    def test_racine_qualifiee_enfants_a_degrader(self):
        """XSD unqualified mais XML entierement qualifie : on retire le ns des enfants."""
        xsd = ('<?xml version="1.0"?>'
               '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
               'xmlns="urn:t" targetNamespace="urn:t" elementFormDefault="unqualified">'
               '<xs:element name="R"><xs:complexType><xs:sequence>'
               '<xs:element name="A" type="xs:string"/>'
               '</xs:sequence></xs:complexType></xs:element></xs:schema>')
        result = run(xsd, '<R><A>1</A></R>')
        self.assertEqual(result.status, STATUS_FIXED)
        from lxml import etree
        root = etree.fromstring(result.corrected)
        self.assertEqual(root.tag, "{urn:t}R")
        self.assertEqual(root[0].tag, "A")   # l'enfant reste sans espace de noms

    def test_imbrication_profonde(self):
        xsd = HEAD + '''
          <xs:element name="R"><xs:complexType><xs:sequence>
            <xs:element name="N" maxOccurs="unbounded"><xs:complexType><xs:sequence>
              <xs:element name="A" type="xs:string"/>
              <xs:element name="B" type="xs:string"/>
            </xs:sequence></xs:complexType></xs:element>
          </xs:sequence></xs:complexType></xs:element>
        </xs:schema>'''
        xml = ('<R xmlns="urn:t"><N><B>1</B><A>1</A></N>'
               '<N><B>2</B><A>2</A></N></R>')
        result = run(xsd, xml)
        self.assertEqual(result.status, STATUS_FIXED)
        from lxml import etree
        root = etree.fromstring(result.corrected)
        for node in root:
            self.assertEqual([etree.QName(c).localname for c in node], ["A", "B"])


class TestNamespace(unittest.TestCase):

    def test_ajout_du_namespace(self):
        xsd = HEAD + '''
          <xs:element name="R"><xs:complexType><xs:sequence>
            <xs:element name="A" type="xs:string"/>
          </xs:sequence></xs:complexType></xs:element>
        </xs:schema>'''
        result = run(xsd, '<R><A>1</A></R>')
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertIn(b'xmlns="urn:t"', result.corrected)

    def test_suppression_du_namespace(self):
        xsd = ('<?xml version="1.0"?>'
               '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
               '<xs:element name="R"><xs:complexType><xs:sequence>'
               '<xs:element name="A" type="xs:string"/>'
               '</xs:sequence></xs:complexType></xs:element></xs:schema>')
        result = run(xsd, '<R xmlns="urn:faux"><A>1</A></R>')
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertNotIn(b"urn:faux", result.corrected)

    def test_namespace_desactive(self):
        xsd = HEAD + '''
          <xs:element name="R"><xs:complexType><xs:sequence>
            <xs:element name="A" type="xs:string"/>
          </xs:sequence></xs:complexType></xs:element>
        </xs:schema>'''
        result = run(xsd, '<R><A>1</A></R>', Options(fix_namespace=False))
        self.assertEqual(result.status, STATUS_FAILED)


class TestOptions(unittest.TestCase):

    XSD = HEAD + '''
      <xs:element name="R"><xs:complexType><xs:sequence>
        <xs:element name="A" type="xs:string"/>
        <xs:element name="B" type="xs:string" minOccurs="0"/>
      </xs:sequence></xs:complexType></xs:element>
    </xs:schema>'''

    def test_ajout_element_obligatoire(self):
        xml = '<R xmlns="urn:t"><B>2</B></R>'
        base = run(self.XSD, xml)
        self.assertEqual(base.status, STATUS_FAILED)
        with_insert = run(self.XSD, xml, Options(insert_missing=True))
        self.assertEqual(with_insert.status, STATUS_FIXED)
        self.assertEqual(order_of(with_insert), ["A", "B"])

    def test_balise_ajoutee_signalee_par_un_commentaire(self):
        """Une balise insérée vide doit se repérer dans le fichier : sans
        commentaire, elle passerait inaperçue à la relecture."""
        from xsdfix.corrector import INSERTED_COMMENT
        xml = '<R xmlns="urn:t"><B>2</B></R>'
        result = run(self.XSD, xml, Options(insert_missing=True))
        self.assertEqual(result.status, STATUS_FIXED)
        texte = result.corrected.decode()
        self.assertIn(INSERTED_COMMENT.strip(), texte)
        # le commentaire précède la balise qu'il annonce
        self.assertLess(texte.index("AJOUTÉ PAR LE"), texte.index("<A/>"))

    def test_commentaire_desactivable(self):
        xml = '<R xmlns="urn:t"><B>2</B></R>'
        result = run(self.XSD, xml, Options(insert_missing=True, comment_inserted=False))
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertNotIn(b"AJOUT", result.corrected)

    def test_commentaire_suit_sa_balise_au_reordonnancement(self):
        """Le commentaire est rattaché à l'élément qui le suit : un tri
        ultérieur ne doit pas les séparer."""
        from lxml import etree
        xsd = HEAD + '''
          <xs:element name="R"><xs:complexType><xs:sequence>
            <xs:element name="A" type="xs:string"/>
            <xs:element name="B" type="xs:string"/>
            <xs:element name="C" type="xs:string"/>
          </xs:sequence></xs:complexType></xs:element>
        </xs:schema>'''
        # B manquant, et C avant A : insertion et réordonnancement dans la foulée
        premier = run(xsd, '<R xmlns="urn:t"><C>3</C><A>1</A></R>',
                      Options(insert_missing=True))
        self.assertEqual(premier.status, STATUS_FIXED)
        # on repasse le fichier corrigé dans l'outil : rien ne doit bouger
        second = run(xsd, premier.corrected.decode(), Options(insert_missing=True))
        self.assertEqual(second.status, STATUS_VALID)

        root = etree.fromstring(premier.corrected)
        noeuds = list(root)
        commentaire = [i for i, n in enumerate(noeuds) if not isinstance(n.tag, str)]
        self.assertTrue(commentaire, "le commentaire doit être présent")
        suivant = noeuds[commentaire[0] + 1]
        self.assertEqual(etree.QName(suivant).localname, "B")

    def test_suppression_balise_inconnue(self):
        xml = '<R xmlns="urn:t"><A>1</A><Inconnu>x</Inconnu></R>'
        base = run(self.XSD, xml)
        self.assertEqual(base.status, STATUS_FAILED)
        with_remove = run(self.XSD, xml, Options(remove_unknown=True))
        self.assertEqual(with_remove.status, STATUS_FIXED)
        self.assertNotIn(b"Inconnu", with_remove.corrected)

    def test_nettoyage_des_valeurs(self):
        xsd = HEAD + '''
          <xs:simpleType name="Code"><xs:restriction base="xs:string">
            <xs:pattern value="[A-Z]{2}"/>
          </xs:restriction></xs:simpleType>
          <xs:element name="R"><xs:complexType><xs:sequence>
            <xs:element name="A" type="Code"/>
          </xs:sequence></xs:complexType></xs:element>
        </xs:schema>'''
        result = run(xsd, '<R xmlns="urn:t"><A>  FR  </A></R>')
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertIn(b"<A>FR</A>", result.corrected)


class TestPreservation(unittest.TestCase):
    """Le correcteur ne doit rien perdre de ce qu'il ne corrige pas."""

    XSD = HEAD + '''
      <xs:element name="R"><xs:complexType><xs:sequence>
        <xs:element name="A"><xs:complexType>
          <xs:simpleContent><xs:extension base="xs:string">
            <xs:attribute name="lang" type="xs:string"/>
          </xs:extension></xs:simpleContent>
        </xs:complexType></xs:element>
        <xs:element name="B" type="xs:string"/>
      </xs:sequence>
      <xs:attribute name="id" type="xs:string"/>
      </xs:complexType></xs:element>
    </xs:schema>'''

    def test_attributs_conserves(self):
        xml = '<R xmlns="urn:t" id="42"><B>b</B><A lang="fr">a</A></R>'
        result = run(self.XSD, xml)
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertIn(b'id="42"', result.corrected)
        self.assertIn(b'lang="fr"', result.corrected)

    def test_commentaires_conserves(self):
        xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!-- en-tete -->\n'
               '<R xmlns="urn:t">\n  <B>b</B>\n  <!-- note -->\n  <A>a</A>\n</R>\n')
        result = run(self.XSD, xml)
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertIn(b"<!-- en-tete -->", result.corrected)
        self.assertIn(b"<!-- note -->", result.corrected)

    def test_encodage_conserve(self):
        xml = ('<?xml version="1.0" encoding="ISO-8859-1"?>'
               '<R xmlns="urn:t"><B>café</B><A>hôtel</A></R>').encode("iso-8859-1")
        report = analyze([InputFile("main.xsd", self.XSD.encode("utf-8"))],
                         [InputFile("doc.xml", xml)], Options())
        result = report.results[0]
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertIn(b"ISO-8859-1", result.corrected)
        self.assertIn("café".encode("iso-8859-1"), result.corrected)

    def test_xml_mal_forme(self):
        report = analyze([InputFile("main.xsd", self.XSD.encode("utf-8"))],
                         [InputFile("doc.xml", b"<R><A>a</R>")], Options())
        self.assertEqual(report.results[0].status, "error")
        self.assertIn("mal formé", report.results[0].fatal)


class TestDiagnostic(unittest.TestCase):
    """Le rapport doit rester informatif même quand la racine est rejetée."""

    XSD = HEAD + '''
      <xs:element name="R"><xs:complexType><xs:sequence>
        <xs:element name="A" type="xs:string"/>
        <xs:element name="B" type="xs:string"/>
      </xs:sequence></xs:complexType></xs:element>
    </xs:schema>'''

    def test_racine_rejetee_ne_masque_pas_les_erreurs_internes(self):
        """Une racine invalide fait s'arrêter lxml : corriger la racine révèle
        les erreurs qu'elle masquait. Le compte d'erreurs augmente alors que le
        fichier s'améliore — le rapport ne doit rien jeter."""
        xml = '<R><B>2</B><A>1</A><Inconnu/></R>'      # pas de namespace + ordre + balise inconnue
        result = run(self.XSD, xml)
        self.assertEqual(len(result.errors_before), 1)          # lxml s'arrête à la racine
        self.assertEqual(result.errors_before[0].category, "racine")
        self.assertEqual(result.status, STATUS_PARTIAL)
        self.assertTrue(result.changes, "les corrections doivent être rapportées")
        self.assertTrue(result.errors_after, "les erreurs révélées doivent être rapportées")
        self.assertIsNotNone(result.corrected)
        self.assertIn("racine était rejeté", result.note or "")

    def test_message_racine_liste_les_racines_acceptees(self):
        result = run(self.XSD, '<Autre xmlns="urn:t"><A>1</A></Autre>')
        message = result.errors_before[0].label
        self.assertIn("<Autre>", message)
        self.assertIn("urn:t", message)          # l'espace de noms attendu est cité
        self.assertIn("<R>", message)            # la racine acceptée est citée

    def test_aucune_correction_possible(self):
        result = run(self.XSD, '<Autre xmlns="urn:t"/>')
        self.assertEqual(result.status, STATUS_FAILED)
        self.assertEqual(result.changes, [])


class TestManquantContreOrdre(unittest.TestCase):
    """lxml emploie le même message pour « balise mal placée » et « balise
    obligatoire absente » : il signale l'élément sur lequel il bute, pas celui
    qui manque. Les deux diagnostics n'appellent pourtant pas la même action."""

    XSD = HEAD + '''
      <xs:element name="Reglement"><xs:complexType><xs:sequence>
        <xs:element name="Code" type="xs:string"/>
        <xs:element name="Reference" type="xs:string" minOccurs="0"/>
        <xs:element name="Compte" type="xs:string"/>
      </xs:sequence></xs:complexType></xs:element>
    </xs:schema>'''

    def test_element_absent_classe_comme_manquant(self):
        xml = '<Reglement xmlns="urn:t"><Reference>R1</Reference><Compte>FR76</Compte></Reglement>'
        result = run(self.XSD, xml)
        erreur = result.errors_before[0]
        self.assertEqual(erreur.category, "manquant")
        self.assertIn("<Code> manque", erreur.label)
        self.assertIn("pas un problème d'ordre", erreur.label)
        self.assertEqual(result.changes, [], "rien à réordonner ici")

    def test_element_mal_place_reste_un_ordre(self):
        xml = '<Reglement xmlns="urn:t"><Compte>FR76</Compte><Code>30</Code></Reglement>'
        result = run(self.XSD, xml)
        self.assertEqual(result.errors_before[0].category, "ordre")
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertEqual(order_of(result), ["Code", "Compte"])

    def test_l_option_conseillee_resout_le_manquant(self):
        """Le message renvoie vers « Ajouter les éléments obligatoires
        manquants » : encore faut-il que cette option règle le cas."""
        xml = '<Reglement xmlns="urn:t"><Reference>R1</Reference><Compte>FR76</Compte></Reglement>'
        result = run(self.XSD, xml, Options(insert_missing=True))
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertEqual(order_of(result), ["Code", "Reference", "Compte"])


class TestNamespacePrudence(unittest.TestCase):
    """Retirer un espace de noms est destructeur : à n'oser qu'à coup sûr."""

    # XSD sans targetNamespace, face à un XML multi-espaces (cas type UBL)
    XSD_SANS_NS = ('<?xml version="1.0"?>'
                   '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
                   '<xs:element name="Invoice"><xs:complexType><xs:sequence>'
                   '<xs:element name="ID" type="xs:string"/>'
                   '</xs:sequence></xs:complexType></xs:element></xs:schema>')

    XML_UBL = ('<Invoice xmlns="urn:ubl:Invoice-2" xmlns:cbc="urn:ubl:cbc">'
               '<cbc:UBLVersionID>2.1</cbc:UBLVersionID>'
               '<cbc:ID>F1</cbc:ID></Invoice>')

    def test_pas_de_suppression_sur_document_multi_espaces(self):
        result = run(self.XSD_SANS_NS, self.XML_UBL)
        self.assertEqual(result.status, STATUS_FAILED)
        self.assertEqual(result.changes, [], "aucun espace de noms ne doit être retiré")
        self.assertIsNone(result.corrected)

    def test_le_message_explique_l_incompatibilite(self):
        result = run(self.XSD_SANS_NS, self.XML_UBL)
        message = result.errors_before[0].label
        self.assertIn("aucun targetNamespace", message)
        self.assertIn("urn:ubl:Invoice-2", message)

    def test_suppression_permise_si_un_seul_espace_de_noms(self):
        """Un document mono-espace face à un XSD sans targetNamespace : là,
        retirer l'espace de noms est le seul geste raisonnable."""
        xml = '<Invoice xmlns="urn:ubl:Invoice-2"><ID>F1</ID></Invoice>'
        result = run(self.XSD_SANS_NS, xml)
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertNotIn(b"urn:ubl", result.corrected)

    def test_balise_connue_mais_mal_qualifiee(self):
        """Un nom déclaré dans le XSD mais utilisé dans un autre espace de noms
        n'est pas une « balise inconnue »."""
        xsd = HEAD + '''
          <xs:element name="R"><xs:complexType><xs:sequence>
            <xs:element name="A" type="xs:string"/>
            <xs:element name="B" type="xs:string"/>
          </xs:sequence></xs:complexType></xs:element>
        </xs:schema>'''
        xml = ('<R xmlns="urn:t" xmlns:autre="urn:autre">'
               '<autre:A>1</autre:A><B>2</B></R>')
        result = run(xsd, xml)
        messages = " ".join(e.label for e in result.errors_before + result.errors_after)
        self.assertIn("autre espace de noms", messages)
        self.assertNotIn("n'est déclaré nulle part", messages)


class TestSchemaMultiFichiers(unittest.TestCase):

    def test_import_multi_namespaces_prefixes_conserves(self):
        """Cas type Factur-X / UBL : racine dans un namespace, enfants dans un autre."""
        main = ('<?xml version="1.0"?>'
                '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
                'xmlns:rsm="urn:rsm" xmlns:ram="urn:ram" targetNamespace="urn:rsm" '
                'elementFormDefault="qualified">'
                '<xs:import namespace="urn:ram" schemaLocation="ram.xsd"/>'
                '<xs:element name="Facture"><xs:complexType><xs:sequence>'
                '<xs:element ref="ram:Numero"/>'
                '<xs:element ref="ram:Date"/>'
                '<xs:element ref="ram:Montant"/>'
                '</xs:sequence></xs:complexType></xs:element></xs:schema>')
        ram = ('<?xml version="1.0"?>'
               '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
               'targetNamespace="urn:ram" elementFormDefault="qualified">'
               '<xs:element name="Numero" type="xs:string"/>'
               '<xs:element name="Date" type="xs:date"/>'
               '<xs:element name="Montant" type="xs:decimal"/>'
               '</xs:schema>')
        xml = ('<rsm:Facture xmlns:rsm="urn:rsm" xmlns:ram="urn:ram">'
               '<ram:Montant>10.00</ram:Montant>'
               '<ram:Numero>F1</ram:Numero>'
               '<ram:Date>2026-01-01</ram:Date>'
               '</rsm:Facture>')
        result = run(main, xml, extra_xsd={"ram.xsd": ram})
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertEqual(order_of(result), ["Numero", "Date", "Montant"])
        # les prefixes d'origine doivent survivre a la correction
        self.assertIn(b"<ram:Numero>", result.corrected)
        self.assertIn(b"<rsm:Facture", result.corrected)

    def test_import_par_chemin_relatif_fichiers_a_plat(self):
        """Cas UBL : le XSD importe « ../common/x.xsd » mais les fichiers ont été
        déposés à plat. Les chemins doivent être réparés automatiquement."""
        main = ('<?xml version="1.0"?>'
                '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
                'xmlns:c="urn:c" targetNamespace="urn:t" xmlns="urn:t" '
                'elementFormDefault="qualified">'
                '<xs:import namespace="urn:c" schemaLocation="../common/types.xsd"/>'
                '<xs:element name="R"><xs:complexType><xs:sequence>'
                '<xs:element ref="c:A"/><xs:element ref="c:B"/>'
                '</xs:sequence></xs:complexType></xs:element></xs:schema>')
        common = ('<?xml version="1.0"?>'
                  '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
                  'targetNamespace="urn:c" elementFormDefault="qualified">'
                  '<xs:element name="A" type="xs:string"/>'
                  '<xs:element name="B" type="xs:string"/></xs:schema>')
        xml = ('<R xmlns="urn:t" xmlns:c="urn:c">'
               '<c:B>2</c:B><c:A>1</c:A></R>')
        result = run(main, xml, extra_xsd={"types.xsd": common})   # noms à plat
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertEqual(order_of(result), ["A", "B"])

    def test_import_manquant_signale_le_fichier(self):
        main = ('<?xml version="1.0"?>'
                '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
                'targetNamespace="urn:t" xmlns="urn:t">'
                '<xs:import namespace="urn:c" schemaLocation="common/types.xsd"/>'
                '<xs:element name="R" type="xs:string"/></xs:schema>')
        report = analyze([InputFile("main.xsd", main.encode())],
                         [InputFile("d.xml", b'<R xmlns="urn:t"/>')], Options())
        self.assertTrue(any("types.xsd" in w for w in report.schema_warnings),
                        report.schema_warnings)

    def test_include(self):
        main = (HEAD + '<xs:include schemaLocation="types.xsd"/>'
                '<xs:element name="R" type="RType"/></xs:schema>')
        types = (HEAD + '<xs:complexType name="RType"><xs:sequence>'
                 '<xs:element name="A" type="xs:string"/>'
                 '<xs:element name="B" type="xs:string"/>'
                 '</xs:sequence></xs:complexType></xs:schema>')
        result = run(main, '<R xmlns="urn:t"><B>2</B><A>1</A></R>',
                     extra_xsd={"types.xsd": types})
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertEqual(order_of(result), ["A", "B"])


class TestConvertisseur(unittest.TestCase):
    """XSD « à plat » généré depuis un XML (Liquid Technologies & consorts) :
    les préfixes sont devenus des morceaux de noms, l'espace de noms a disparu.
    Le convertisseur doit rendre le schéma utilisable sans toucher à l'ordre."""

    PLAT = ('<?xml version="1.0"?>'
            '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" '
            'elementFormDefault="qualified">'
            '<xs:element name="ubl.Invoice"><xs:complexType><xs:sequence>'
            '<xs:element name="cbc.ID" type="xs:short"/>'
            '<xs:element name="cbc.IssueDate" type="xs:date"/>'
            '<xs:element minOccurs="0" name="cac.OrderReference">'
            '<xs:complexType><xs:sequence>'
            '<xs:element name="cbc.ID" type="xs:string"/>'
            '<xs:element name="cbc.SalesOrderID" type="xs:string"/>'
            '</xs:sequence></xs:complexType></xs:element>'
            '</xs:sequence></xs:complexType></xs:element></xs:schema>')

    NS = {
        "ubl": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
        "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
        "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    }

    XML_DESORDRE = (
        '<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" '
        'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2" '
        'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">'
        '<cbc:IssueDate>2026-07-31</cbc:IssueDate>'      # après ID selon le XSD
        '<cbc:ID>12</cbc:ID>'
        '<cac:OrderReference>'
        '<cbc:SalesOrderID>X</cbc:SalesOrderID>'          # après ID selon le XSD
        '<cbc:ID>PO-1</cbc:ID>'
        '</cac:OrderReference></Invoice>')

    def _convertir(self):
        import tempfile
        from convertir_xsd import Converter
        workdir = tempfile.mkdtemp(prefix="conv_")
        source = os.path.join(workdir, "plat.xsd")
        with open(source, "w", encoding="utf-8") as handle:
            handle.write(self.PLAT)
        converter = Converter(dict(self.NS))
        converter.read(source)
        paths = converter.write(converter.merge(), os.path.join(workdir, "out"))
        files = []
        for path in paths:
            with open(path, "rb") as handle:
                files.append(InputFile(os.path.basename(path), handle.read()))
        return converter, files

    def test_la_racine_retrouve_son_espace_de_noms(self):
        converter, files = self._convertir()
        self.assertEqual(converter.root, ("ubl", "Invoice"))
        self.assertEqual({f.name for f in files}, {"ubl.xsd", "cbc.xsd", "cac.xsd"})

    def test_conflit_de_types_reconcilie(self):
        """cbc.ID vaut xs:short en tête et xs:string plus bas : en XSD un élément
        global n'a qu'un type, il faut trancher sans casser la validation."""
        converter, _ = self._convertir()
        self.assertTrue(any("cbc:ID" in c for c in converter.conflicts), converter.conflicts)

    def test_separateurs_multiples_et_noms_invalides(self):
        """Certains générateurs alignent plusieurs séparateurs. Le reste ne doit
        pas se retrouver dans le nom : « .DespatchDocumentReference » n'est pas
        un nom XSD valide et ferait échouer la compilation."""
        from xsdfix.flat_schema import compile_check, convert, split_prefixed
        self.assertEqual(split_prefixed("cac...DespatchDocumentReference"),
                         ("cac", "DespatchDocumentReference"))
        self.assertEqual(split_prefixed("cac. .Truc"), ("cac", "Truc"))

        plat = ('<?xml version="1.0"?>'
                '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
                '<xs:element name="ubl.Invoice"><xs:complexType><xs:sequence>'
                '<xs:element name="cbc.ID" type="xs:string"/>'
                '<xs:element name="cac...DespatchDocumentReference" type="xs:string"/>'
                '<xs:element name="cbc.4Ligne" type="xs:string"/>'
                '</xs:sequence></xs:complexType></xs:element></xs:schema>')
        fichiers, racine, notes = convert(plat.encode(), dict(self.NS))
        noms = {n for n, _ in fichiers}
        self.assertIn("cac.xsd", noms)
        # le schéma produit doit compiler : c'est le vrai critère
        self.assertIsNone(compile_check(fichiers, "%s.xsd" % racine[0]))
        contenu = b"".join(d for _, d in fichiers)
        self.assertIn(b'name="DespatchDocumentReference"', contenu)
        self.assertNotIn(b'name=".DespatchDocumentReference"', contenu)

    # cbc.ID est numérique en tête de facture (le générateur l'a déduit d'un
    # exemple où l'identifiant était un nombre) et textuel dans OrderReference
    PLAT_TYPES_DIVERGENTS = (
        '<?xml version="1.0"?>'
        '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
        '<xs:element name="ubl.Invoice"><xs:complexType><xs:sequence>'
        '<xs:element name="cbc.ID"><xs:complexType><xs:simpleContent>'
        '<xs:extension base="xs:short">'
        '<xs:attribute name="schemeID" type="xs:string"/>'
        '</xs:extension></xs:simpleContent></xs:complexType></xs:element>'
        '<xs:element name="cac.OrderReference"><xs:complexType><xs:sequence>'
        '<xs:element name="cbc.ID" type="xs:string"/>'
        '</xs:sequence></xs:complexType></xs:element>'
        '</xs:sequence></xs:complexType></xs:element></xs:schema>')

    def test_type_le_plus_permissif_retenu(self):
        """Un même nom typé xs:short ici et xs:string là ne doit pas hériter du
        type numérique : toutes les valeurs textuelles seraient rejetées."""
        from xsdfix.flat_schema import convert
        fichiers, _, notes = convert(self.PLAT_TYPES_DIVERGENTS.encode(), dict(self.NS),
                                     relax_types=False)
        cbc = dict(fichiers)["cbc.xsd"]
        self.assertIn(b'base="xs:string"', cbc)
        self.assertNotIn(b'base="xs:short"', cbc)
        self.assertTrue(any("types divergents" in n for n in notes), notes)

    def test_valeur_textuelle_acceptee_apres_conversion(self):
        """Le cas de bout en bout : un ID textuel ne doit plus être signalé."""
        from xsdfix.flat_schema import convert
        for relax in (False, True):
            fichiers, _, _ = convert(self.PLAT_TYPES_DIVERGENTS.encode(), dict(self.NS),
                                     relax_types=relax)
            files = [InputFile(n, d) for n, d in fichiers]
            xml = ('<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" '
                   'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:'
                   'CommonBasicComponents-2" '
                   'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:'
                   'CommonAggregateComponents-2">'
                   '<cbc:ID>FA-2026-001</cbc:ID>'          # textuel, pas numérique
                   '<cac:OrderReference><cbc:ID>PO-1</cbc:ID></cac:OrderReference>'
                   '</Invoice>')
            report = analyze(files, [InputFile("f.xml", xml.encode())],
                             Options(), preferred_xsd="ubl.xsd")
            result = report.results[0]
            self.assertEqual(result.status, STATUS_VALID,
                             "types assouplis=%s : %s" % (
                                 relax, [e.label for e in result.errors_before]))

    def test_types_assouplis_ignorent_les_formats_devines(self):
        """Avec l'assouplissement, une date fantaisiste ne fait plus échouer un
        fichier dont le seul vrai défaut serait l'ordre des balises."""
        from xsdfix.flat_schema import convert
        plat = ('<?xml version="1.0"?>'
                '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
                '<xs:element name="ubl.Invoice"><xs:complexType><xs:sequence>'
                '<xs:element name="cbc.IssueDate" type="xs:date"/>'
                '<xs:element name="cbc.ID" type="xs:short"/>'
                '</xs:sequence></xs:complexType></xs:element></xs:schema>')
        xml = ('<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" '
               'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:'
               'CommonBasicComponents-2">'
               '<cbc:ID>FA-001</cbc:ID>'                  # textuel
               '<cbc:IssueDate>31/07/2026</cbc:IssueDate>'  # format non ISO
               '</Invoice>')

        fichiers, _, _ = convert(plat.encode(), dict(self.NS), relax_types=True)
        report = analyze([InputFile(n, d) for n, d in fichiers],
                         [InputFile("f.xml", xml.encode())], Options(),
                         preferred_xsd="ubl.xsd")
        result = report.results[0]
        self.assertEqual(result.status, STATUS_FIXED)      # seul l'ordre est corrigé
        self.assertEqual(order_of(result), ["IssueDate", "ID"])
        self.assertEqual(len(result.errors_after), 0)

    def test_ordre_detecte_et_corrige_apres_conversion(self):
        _, files = self._convertir()
        report = analyze(files, [InputFile("f.xml", self.XML_DESORDRE.encode())],
                         Options(), preferred_xsd="ubl.xsd")
        self.assertIsNone(report.schema_error)
        result = report.results[0]
        self.assertEqual(result.status, STATUS_FIXED)
        self.assertEqual(len(result.errors_after), 0)
        self.assertEqual(order_of(result), ["ID", "IssueDate", "OrderReference"])


class TestReferentiel(unittest.TestCase):
    """Contrôle des données portées par les balises, face à un classeur de
    référence. Le XSD dit qu'une balise existe ; lui seul ne dira jamais que
    le numéro de TVA doit valoir 3145 et non 11234."""

    XML = ('<Invoice xmlns="urn:ubl:Invoice-2" xmlns:cac="urn:ubl:cac" '
           'xmlns:cbc="urn:ubl:cbc">'
           '<cac:AccountingSupplierParty><cac:Party><cac:PartyTaxScheme>'
           '<cbc:CompanyID>11234</cbc:CompanyID>'
           '</cac:PartyTaxScheme></cac:Party></cac:AccountingSupplierParty>'
           '<cac:AccountingCustomerParty><cac:Party>'
           '<cac:PartyIdentification><cbc:ID>1084</cbc:ID></cac:PartyIdentification>'
           '<cac:PartyTaxScheme><cbc:CompanyID>FR99999999999</cbc:CompanyID>'
           '</cac:PartyTaxScheme></cac:Party></cac:AccountingCustomerParty>'
           '</Invoice>')

    def _racine(self):
        from lxml import etree
        return etree.fromstring(self.XML.encode())

    def _controler(self, lignes):
        from xsdfix.referentiel import charger_regles, controler
        regles, soucis = charger_regles(lignes)
        self.assertEqual(soucis, [], "le classeur doit être lisible")
        return controler(self._racine(), regles)

    ENTETE = ["Chemin", "Valeur attendue", "Balise clé", "Valeur clé", "Commentaire"]

    def test_valeur_constante(self):
        ecarts = self._controler([self.ENTETE,
                                  ["AccountingSupplierParty//CompanyID", "3145", "", "", ""]])
        self.assertEqual(len(ecarts), 1)
        self.assertEqual(ecarts[0].actuel, "11234")
        self.assertEqual(ecarts[0].attendu, "3145")

    def test_meme_balise_deux_emplacements(self):
        """Le cœur du problème : CompanyID existe chez le vendeur et chez le
        client. Deux règles distinctes doivent viser chacune la bonne."""
        ecarts = self._controler([
            self.ENTETE,
            ["AccountingSupplierParty//CompanyID", "11234", "", "", ""],   # correct
            ["AccountingCustomerParty//CompanyID", "FR55987654321", "", "", ""],
        ])
        self.assertEqual(len(ecarts), 1, [e.label for e in ecarts])
        self.assertIn("AccountingCustomerParty", ecarts[0].chemin)

    def test_regle_conditionnee_par_une_cle(self):
        regles = [self.ENTETE,
                  ["AccountingCustomerParty//CompanyID", "FR55987654321",
                   "AccountingCustomerParty//PartyIdentification/ID", "1084", ""],
                  ["AccountingCustomerParty//CompanyID", "FR12000000009",
                   "AccountingCustomerParty//PartyIdentification/ID", "2201", ""]]
        ecarts = self._controler(regles)
        # seule la règle du client 1084 s'applique à ce fichier
        self.assertEqual(len(ecarts), 1)
        self.assertEqual(ecarts[0].attendu, "FR55987654321")

    def test_regle_ambigue_refuse_de_deviner(self):
        ecarts = self._controler([self.ENTETE, ["CompanyID", "XXX", "", "", ""]])
        self.assertEqual(len(ecarts), 1)
        self.assertTrue(ecarts[0].ambigu)
        self.assertEqual(len(ecarts[0].candidats), 2)
        self.assertIn("Précisez le chemin", ecarts[0].label)

    def test_condition_incomplete_signalee(self):
        from xsdfix.referentiel import charger_regles
        regles, soucis = charger_regles([self.ENTETE,
                                         ["CompanyID", "X", "UneBalise", "", ""]])
        self.assertEqual(regles, [])
        self.assertTrue(any("condition est incomplète" in s for s in soucis), soucis)

    def test_chemin_exact_et_nom_seul(self):
        from xsdfix.referentiel import trouver
        racine = self._racine()
        self.assertEqual(len(trouver(racine, "CompanyID")), 2)
        self.assertEqual(len(trouver(
            racine,
            "/Invoice/AccountingSupplierParty/Party/PartyTaxScheme/CompanyID")), 1)
        self.assertEqual(len(trouver(racine, "AccountingCustomerParty//CompanyID")), 1)

    def test_le_fichier_n_est_jamais_modifie(self):
        """Un écart de données se signale, il ne se corrige pas."""
        xsd = HEAD + '''
          <xs:element name="R"><xs:complexType><xs:sequence>
            <xs:element name="A" type="xs:string"/>
          </xs:sequence></xs:complexType></xs:element>
        </xs:schema>'''
        from xsdfix.referentiel import charger_regles
        regles, _ = charger_regles([["Chemin", "Valeur attendue"], ["A", "bonne"]])
        report = analyze([InputFile("s.xsd", xsd.encode())],
                         [InputFile("f.xml", b'<R xmlns="urn:t"><A>mauvaise</A></R>')],
                         Options(), regles=regles)
        result = report.results[0]
        self.assertEqual(result.status, STATUS_VALID)      # conforme au XSD
        self.assertEqual(len(result.ecarts), 1)            # mais donnée non conforme
        self.assertIsNone(result.corrected)                # rien n'a été réécrit

    def test_lecture_xlsx_et_csv(self):
        from xsdfix.referentiel import generer_modele, lire_classeur
        classeur = generer_modele([self._racine()])
        lignes = lire_classeur(classeur, "modele.xlsx")
        self.assertEqual(lignes[0][:2], ["Chemin", "Valeur actuelle"])
        chemins = [l[0] for l in lignes[1:]]
        self.assertIn("/Invoice/AccountingSupplierParty/Party/PartyTaxScheme/CompanyID",
                      chemins)

        csv_data = "Chemin;Valeur attendue\nCompanyID;3145\n".encode("utf-8")
        self.assertEqual(lire_classeur(csv_data, "ref.csv")[1], ["CompanyID", "3145"])


if __name__ == "__main__":
    unittest.main()
