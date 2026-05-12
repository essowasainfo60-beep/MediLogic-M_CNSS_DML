import os
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_cors import CORS
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import hashlib

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'MediLogic_Secret_2026')
CORS(app)

DATABASE_URL = os.getenv('DATABASE_URL')

def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

# ==================== FONCTIONS UTILITAIRES ====================
def calculer_mois_retard(id_membre):
    """Calcule les mois de retard depuis janvier de l'année courante"""
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("SELECT id, nom FROM membres WHERE id = %s", (id_membre,))
    membre = cur.fetchone()
    if not membre:
        return {'mois_retard': 0, 'dette_totale': 0}
    
    annee = datetime.now().year
    mois_courant = datetime.now().month
    jour_courant = datetime.now().day
    
    # Récupérer tous les mois payés
    cur.execute("""
        SELECT generate_series(
            mois_debut, 
            mois_fin, 
            '1 month'::interval
        ) as mois_paye
        FROM cotisations
        WHERE id_membre = %s AND EXTRACT(YEAR FROM mois_debut) = %s
    """, (id_membre, annee))
    
    mois_payes = set()
    for row in cur.fetchall():
        if row['mois_paye']:
            mois_payes.add(int(row['mois_paye'].month))
    
    cur.close()
    conn.close()
    
    # Calculer les mois impayés
    mois_impayes = []
    for mois in range(1, mois_courant + 1):
        if mois not in mois_payes:
            if mois == mois_courant and jour_courant <= 5:
                continue  # Pas encore en retard
            mois_impayes.append(mois)
    
    dette_totale = len(mois_impayes) * 1000
    
    return {'mois_retard': len(mois_impayes), 'dette_totale': dette_totale}

# ==================== PAGES ====================
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/login')
def login():
    return render_template('login.html')

@app.route('/inscription')
def inscription_page():
    return render_template('inscription.html')

@app.route('/membres')
def membres_page():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('membres.html')

@app.route('/cotisations')
def cotisations_page():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('cotisations.html')

@app.route('/caisse')
def caisse_page():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('caisse.html')

@app.route('/historique')
def historique_page():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('historique.html')

# ==================== API AUTHENTIFICATION ====================
@app.route('/api/admin/login', methods=['POST'])
def api_admin_login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    hash_mdp = hashlib.sha256(password.encode()).hexdigest()
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM admin WHERE username = %s AND password_hash = %s", (username, hash_mdp))
    admin = cur.fetchone()
    cur.close()
    conn.close()
    
    if admin:
        session['user_id'] = 0
        session['user_nom'] = 'Administrateur'
        session['user_role'] = 'admin'
        return jsonify({'success': True})
    return jsonify({'success': False})

@app.route('/api/membre/login', methods=['POST'])
def api_membre_login():
    data = request.json
    telephone = data.get('telephone')
    
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, nom, role FROM membres WHERE telephone LIKE %s", (f'%{telephone}%',))
    membre = cur.fetchone()
    cur.close()
    conn.close()
    
    if membre:
        session['user_id'] = membre['id']
        session['user_nom'] = membre['nom']
        session['user_role'] = membre.get('role', 'membre')
        return jsonify({'success': True, 'nom': membre['nom']})
    return jsonify({'success': False, 'message': 'Numéro non trouvé'})

@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/session', methods=['GET'])
def get_session():
    if 'user_id' in session:
        return jsonify({
            'logged_in': True,
            'nom': session.get('user_nom'),
            'role': session.get('user_role')
        })
    return jsonify({'logged_in': False})

# ==================== API INSCRIPTION ====================
@app.route('/api/inscription', methods=['POST'])
def api_inscription():
    data = request.json
    nom = data.get('nom')
    telephone = data.get('telephone')
    
    if not nom or not telephone:
        return jsonify({'success': False, 'message': 'Champs requis'})
    
    conn = get_db()
    cur = conn.cursor()
    
    # Vérifier si existe déjà
    cur.execute("SELECT id FROM membres WHERE telephone = %s", (telephone,))
    if cur.fetchone():
        cur.close()
        conn.close()
        return jsonify({'success': False, 'message': 'Ce numéro existe déjà'})
    
    # Ajouter membre avec frais d'adhésion
    cur.execute("""
        INSERT INTO membres (nom, telephone, frais_adhesion, frais_adhesion_paye, frais_adhesion_reste, adhesion_statut)
        VALUES (%s, %s, 4000, 0, 4000, 'impaye')
        RETURNING id
    """, (nom, telephone))
    membre_id = cur.fetchone()['id']
    
    # Enregistrer le paiement de l'adhésion si payé maintenant
    # (par défaut, impayé - à payer plus tard)
    
    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Inscription réussie'})

# ==================== API MEMBRES ====================
@app.route('/api/membres', methods=['GET'])
def get_membres():
    conn = get_db()
    cur = conn.cursor()
    
    if session.get('user_role') == 'membre':
        cur.execute("SELECT id, nom, telephone, adhesion_statut, frais_adhesion_reste FROM membres WHERE role != 'admin'")
    else:
        cur.execute("SELECT * FROM membres ORDER BY id DESC")
    
    membres = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(membres)

@app.route('/api/membres', methods=['POST'])
def add_membre():
    if session.get('user_role') != 'admin':
        return jsonify({'success': False, 'message': 'Permission refusée'}), 403
    
    data = request.json
    frais = data.get('frais_adhesion', 4000)
    
    conn = get_db()
    cur = conn.cursor()
    
    # Vérifier si existe
    cur.execute("SELECT id FROM membres WHERE telephone = %s", (data['telephone'],))
    if cur.fetchone():
        cur.close()
        conn.close()
        return jsonify({'success': False, 'message': 'Ce numéro existe déjà'})
    
    cur.execute("""
        INSERT INTO membres (nom, telephone, frais_adhesion, frais_adhesion_paye, frais_adhesion_reste, adhesion_statut)
        VALUES (%s, %s, %s, 0, %s, 'impaye')
        RETURNING id
    """, (data['nom'], data['telephone'], frais, frais))
    membre = cur.fetchone()
    
    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({'success': True, 'message': 'Membre ajouté'})

@app.route('/api/membres/<int:id>', methods=['DELETE'])
def delete_membre(id):
    if session.get('user_role') != 'admin':
        return jsonify({'success': False, 'message': 'Permission refusée'}), 403
    
    conn = get_db()
    cur = conn.cursor()
    
    try:
        cur.execute("DELETE FROM cotisations WHERE id_membre = %s", (id,))
        cur.execute("DELETE FROM caisse WHERE source = %s", (str(id),))
        cur.execute("DELETE FROM membres WHERE id = %s", (id,))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)})
    finally:
        cur.close()
        conn.close()

@app.route('/api/membre/statut/<int:id>', methods=['GET'])
def get_membre_statut(id):
    retard = calculer_mois_retard(id)
    return jsonify({
        'actuel': 'À jour' if retard['mois_retard'] == 0 else 'En retard',
        'mois_retard': retard['mois_retard'],
        'dette': retard['dette_totale']
    })

@app.route('/api/membres/retard', methods=['GET'])
def get_retard():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, nom, telephone FROM membres WHERE role != 'admin'")
    membres = cur.fetchall()
    cur.close()
    conn.close()
    
    resultat = []
    for m in membres:
        retard = calculer_mois_retard(m['id'])
        if retard['mois_retard'] > 0:
            resultat.append({
                'id': m['id'],
                'nom': m['nom'],
                'telephone': m['telephone'],
                'mois_retard': retard['mois_retard'],
                'dette_totale': retard['dette_totale']
            })
    
    return jsonify(resultat)

def verifier_periode_deja_payee(id_membre, debut, fin):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT id FROM cotisations 
        WHERE id_membre = %s 
        AND (mois_debut BETWEEN %s AND %s OR mois_fin BETWEEN %s AND %s)
    """, (id_membre, debut, fin, debut, fin))
    result = cur.fetchone()
    cur.close()
    conn.close()
    return result is not None

# ==================== API COTISATIONS ====================
@app.route('/api/cotisations', methods=['POST'])
def add_cotisation():
    if session.get('user_role') != 'admin':
        return jsonify({'success': False, 'message': 'Seul l\'admin peut enregistrer'}), 403
    
    data = request.json
    id_membre = data['id_membre']
    periode = data['periode']  # 1, 3, 6, 12 mois
    mois_debut = data['mois_debut']  # format: '2026-06'
    montant_unitaire = data.get('montant_unitaire', 1000)
    enregistre_par = data['enregistre_par']
    
    # Convertir la date au bon format (YYYY-MM-DD)
    debut = datetime.strptime(mois_debut + '-01', '%Y-%m-%d')
    
    # Calculer la date de fin
    mois_fin = debut + timedelta(days=(periode * 30))
    montant_total = periode * montant_unitaire
    
    conn = get_db()
    cur = conn.cursor()
    
    # Vérifier doublon (format correct)
    cur.execute("""
        SELECT id FROM cotisations 
        WHERE id_membre = %s AND mois_debut = %s
    """, (id_membre, debut))
    if cur.fetchone():
        cur.close()
        conn.close()
        return jsonify({'success': False, 'message': 'Cette période a déjà été payée'})
    
    # Enregistrer cotisation
    cur.execute("""
        INSERT INTO cotisations (id_membre, mois_debut, mois_fin, nombre_mois, montant_total, montant_unitaire, enregistre_par)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (id_membre, debut, mois_fin, periode, montant_total, montant_unitaire, enregistre_par))
    
    # Alimenter caisse
    cur.execute("SELECT COALESCE(solde_apres, 0) FROM caisse ORDER BY id DESC LIMIT 1")
    solde = cur.fetchone()
    solde_actuel = solde['coalesce'] if solde else 0
    nouveau_solde = solde_actuel + montant_total
    
    cur.execute("""
        INSERT INTO caisse (type, montant, motif, source, solde_apres, effectue_par)
        VALUES ('entree', %s, %s, %s, %s, %s)
    """, (montant_total, f"Cotisation {periode} mois a partir de {mois_debut}", str(id_membre), nouveau_solde, enregistre_par))
    
    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({'success': True, 'message': f'{montant_total} FCFA enregistrés pour {periode} mois'})

@app.route('/api/cotisations/historique', methods=['GET'])
def get_historique():
    conn = get_db()
    cur = conn.cursor()
    
    if session.get('user_role') == 'membre':
        cur.execute("""
            SELECT c.*, m.nom as membre_nom, m.telephone 
            FROM cotisations c
            JOIN membres m ON c.id_membre = m.id
            WHERE c.id_membre = %s
            ORDER BY c.date_paiement DESC
        """, (session['user_id'],))
    else:
        cur.execute("""
            SELECT c.*, m.nom as membre_nom, m.telephone 
            FROM cotisations c
            JOIN membres m ON c.id_membre = m.id
            ORDER BY c.date_paiement DESC LIMIT 100
        """)
    
    historique = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(historique)

# ==================== API CAISSE ====================
@app.route('/api/caisse/solde', methods=['GET'])
def get_solde():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(solde_apres, 0) as solde FROM caisse ORDER BY id DESC LIMIT 1")
    solde = cur.fetchone()
    cur.close()
    conn.close()
    return jsonify({'solde': solde['solde'] if solde else 0})

@app.route('/api/caisse/operations', methods=['GET'])
def get_operations():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM caisse ORDER BY date_operation DESC LIMIT 50")
    ops = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(ops)

@app.route('/api/caisse/sortie', methods=['POST'])
def add_sortie():
    if session.get('user_role') != 'admin':
        return jsonify({'success': False, 'message': 'Seul l\'admin peut décaisser'}), 403
    
    data = request.json
    montant = data['montant']
    motif = data['motif']
    
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("SELECT COALESCE(solde_apres, 0) as solde FROM caisse ORDER BY id DESC LIMIT 1")
    solde = cur.fetchone()
    solde_actuel = solde['solde'] if solde else 0
    
    if solde_actuel < montant:
        cur.close()
        conn.close()
        return jsonify({'success': False, 'message': 'Solde insuffisant'})
    
    nouveau_solde = solde_actuel - montant
    cur.execute("""
        INSERT INTO caisse (type, montant, motif, source, solde_apres, effectue_par)
        VALUES ('sortie', %s, %s, %s, %s, %s)
    """, (montant, motif, 'Décaissement', nouveau_solde, session.get('user_nom')))
    
    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({'success': True, 'message': f'Décaissement de {montant} FCFA effectué'})

# ==================== API STATS ====================
@app.route('/api/stats', methods=['GET'])
def get_stats():
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("SELECT COUNT(*) as total FROM membres WHERE role != 'admin'")
    total = cur.fetchone()['total']
    
    # Calculer manuellement les membres à jour (sans retard)
    cur.execute("SELECT id FROM membres WHERE role != 'admin'")
    membres = cur.fetchall()
    
    a_jour = 0
    total_collecte = 0
    
    for m in membres:
        retard = calculer_mois_retard(m['id'])
        if retard['mois_retard'] == 0:
            a_jour += 1
    
    # Total collecté (caisse)
    cur.execute("SELECT COALESCE(solde_apres, 0) as solde FROM caisse ORDER BY id DESC LIMIT 1")
    solde = cur.fetchone()
    
    # Total des cotisations enregistrées
    cur.execute("SELECT COALESCE(SUM(montant_total), 0) as total FROM cotisations")
    total_collecte = cur.fetchone()['total']
    
    cur.close()
    conn.close()
    
    return jsonify({
        'total_membres': total,
        'a_jour': a_jour,
        'en_retard': total - a_jour,
        'total_collecte': total_collecte,
        'solde_caisse': solde['solde'] if solde else 0
    })

@app.route('/api/payer_adhesion', methods=['POST'])
def payer_adhesion():
    if session.get('user_role') != 'admin':
        return jsonify({'success': False, 'message': 'Permission refusée'}), 403
    
    data = request.json
    id_membre = data['id_membre']
    montant = data.get('montant', 4000)
    
    conn = get_db()
    cur = conn.cursor()
    
    # Vérifier le membre
    cur.execute("SELECT nom, frais_adhesion_reste FROM membres WHERE id = %s", (id_membre,))
    membre = cur.fetchone()
    
    if not membre:
        cur.close()
        conn.close()
        return jsonify({'success': False, 'message': 'Membre non trouvé'})
    
    reste = membre['frais_adhesion_reste']
    
    if reste <= 0:
        cur.close()
        conn.close()
        return jsonify({'success': False, 'message': 'Adhésion déjà payée'})
    
    # Mettre à jour
    nouveau_paye = 4000 - reste + montant if reste < 4000 else montant
    nouveau_reste = 4000 - nouveau_paye
    statut = 'payé' if nouveau_reste <= 0 else 'partiel'
    
    cur.execute("""
        UPDATE membres 
        SET frais_adhesion_paye = %s, 
            frais_adhesion_reste = %s, 
            adhesion_statut = %s
        WHERE id = %s
    """, (nouveau_paye, nouveau_reste, statut, id_membre))
    
    # Enregistrer l'opération en caisse
    cur.execute("SELECT COALESCE(solde_apres, 0) FROM caisse ORDER BY id DESC LIMIT 1")
    solde = cur.fetchone()
    solde_actuel = solde['coalesce'] if solde else 0
    nouveau_solde = solde_actuel + montant
    
    cur.execute("""
        INSERT INTO caisse (type, montant, motif, source, solde_apres, effectue_par)
        VALUES ('entree', %s, %s, %s, %s, %s)
    """, (montant, f"Adhésion - {membre['nom']}", str(id_membre), nouveau_solde, session.get('user_nom')))
    
    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({'success': True, 'message': f'Adhésion payée: {montant} FCFA'})

# ==================== LANCEMENT ====================
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)