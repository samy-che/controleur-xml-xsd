"""Moteur de validation et de correction XML vis-a-vis d'un XSD.

Le moteur est independant de toute interface : il s'utilise aussi bien depuis
le serveur web fourni que depuis un script ou un futur backend FastAPI/Flask.

    from xsdfix.service import InputFile, analyze
    from xsdfix.corrector import Options

    rapport = analyze([InputFile("facture.xsd", xsd_bytes)],
                      [InputFile("f1.xml", xml_bytes)],
                      Options())
"""

__version__ = "1.0.0"
