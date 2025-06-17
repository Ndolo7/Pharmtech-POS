"use client"

import type React from "react"
import { useState, useEffect } from "react"
import { useAuth } from "../contexts/AuthContext"
import api from "../services/api"
import toast from "react-hot-toast"
import { Plus, Package, ArrowUpDown } from "lucide-react"

interface Product {
  id: number
  name: string
  barcode: string
  category: number
  category_name: string
  unit_price: number
  cost_price: number
  current_stock: number
  reorder_level: number
  is_active: boolean
}

interface Category {
  id: number
  name: string
}

interface Supplier {
  id: number
  name: string
}

const Products: React.FC = () => {
  const { user } = useAuth()
  const [products, setProducts] = useState<Product[]>([])
  const [categories, setCategories] = useState<Category[]>([])
  const [suppliers, setSuppliers] = useState<Supplier[]>([])
  const [showProductModal, setShowProductModal] = useState(false)
  const [showReceiveModal, setShowReceiveModal] = useState(false)
  const [showTransferModal, setShowTransferModal] = useState(false)
  const [showAdjustModal, setShowAdjustModal] = useState(false)
  const [selectedProduct, setSelectedProduct] = useState<Product | null>(null)
  const [loading, setLoading] = useState(false)

  // Form states
  const [productForm, setProductForm] = useState({
    name: "",
    barcode: "",
    category: "",
    unit_price: "",
    cost_price: "",
    reorder_level: "10",
  })

  const [receiveForm, setReceiveForm] = useState({
    supplier_id: "",
    invoice_number: "",
    items: [{ product_id: "", quantity: "", unit_cost: "" }],
  })

  useEffect(() => {
    fetchProducts()
    fetchCategories()
    fetchSuppliers()
  }, [])

  const fetchProducts = async () => {
    try {
      const response = await api.get("/products/")
      setProducts(response.data.results || response.data)
    } catch (error) {
      toast.error("Error fetching products")
    }
  }

  const fetchCategories = async () => {
    try {
      const response = await api.get("/products/categories/")
      setCategories(response.data.results || response.data)
    } catch (error) {
      toast.error("Error fetching categories")
    }
  }

  const fetchSuppliers = async () => {
    try {
      const response = await api.get("/products/suppliers/")
      setSuppliers(response.data.results || response.data)
    } catch (error) {
      toast.error("Error fetching suppliers")
    }
  }

  const handleCreateProduct = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)

    try {
      await api.post("/products/", {
        ...productForm,
        unit_price: Number.parseFloat(productForm.unit_price),
        cost_price: Number.parseFloat(productForm.cost_price),
        reorder_level: Number.parseInt(productForm.reorder_level),
      })

      toast.success("Product created successfully")
      setShowProductModal(false)
      setProductForm({
        name: "",
        barcode: "",
        category: "",
        unit_price: "",
        cost_price: "",
        reorder_level: "10",
      })
      fetchProducts()
    } catch (error) {
      toast.error("Error creating product")
    } finally {
      setLoading(false)
    }
  }

  const handleReceiveStock = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)

    try {
      const totalAmount = receiveForm.items.reduce((sum, item) => {
        return sum + Number.parseFloat(item.quantity) * Number.parseFloat(item.unit_cost)
      }, 0)

      await api.post("/products/receive/", {
        ...receiveForm,
        total_amount: totalAmount,
        items: receiveForm.items.map((item) => ({
          product_id: Number.parseInt(item.product_id),
          quantity: Number.parseInt(item.quantity),
          unit_cost: Number.parseFloat(item.unit_cost),
        })),
      })

      toast.success("Stock received successfully")
      setShowReceiveModal(false)
      setReceiveForm({
        supplier_id: "",
        invoice_number: "",
        items: [{ product_id: "", quantity: "", unit_cost: "" }],
      })
      fetchProducts()
    } catch (error) {
      toast.error("Error receiving stock")
    } finally {
      setLoading(false)
    }
  }

  const handleAdjustStock = async (productId: number, newQuantity: number, reason: string) => {
    if (!user?.can_adjust_stock()) {
      toast.error("You do not have permission to adjust stock")
      return
    }

    try {
      await api.post("/products/adjust/", {
        product_id: productId,
        new_quantity: newQuantity,
        reason,
      })

      toast.success("Stock adjusted successfully")
      setShowAdjustModal(false)
      fetchProducts()
    } catch (error) {
      toast.error("Error adjusting stock")
    }
  }

  const addReceiveItem = () => {
    setReceiveForm({
      ...receiveForm,
      items: [...receiveForm.items, { product_id: "", quantity: "", unit_cost: "" }],
    })
  }

  const updateReceiveItem = (index: number, field: string, value: string) => {
    const newItems = [...receiveForm.items]
    newItems[index] = { ...newItems[index], [field]: value }
    setReceiveForm({ ...receiveForm, items: newItems })
  }

  const removeReceiveItem = (index: number) => {
    const newItems = receiveForm.items.filter((_, i) => i !== index)
    setReceiveForm({ ...receiveForm, items: newItems })
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold text-gray-900">Products</h1>
        <div className="flex space-x-3">
          <button
            onClick={() => setShowReceiveModal(true)}
            className="bg-green-600 text-white px-4 py-2 rounded-md hover:bg-green-700 flex items-center"
          >
            <Package className="mr-2 h-4 w-4" />
            Receive Stock
          </button>
          <button
            onClick={() => setShowProductModal(true)}
            className="bg-blue-600 text-white px-4 py-2 rounded-md hover:bg-blue-700 flex items-center"
          >
            <Plus className="mr-2 h-4 w-4" />
            Add Product
          </button>
        </div>
      </div>

      {/* Products Table */}
      <div className="bg-white shadow rounded-lg overflow-hidden">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Product
              </th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Category
              </th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Price</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Stock</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                Actions
              </th>
            </tr>
          </thead>
          <tbody className="bg-white divide-y divide-gray-200">
            {products.map((product) => (
              <tr key={product.id} className={product.current_stock <= product.reorder_level ? "bg-red-50" : ""}>
                <td className="px-6 py-4 whitespace-nowrap">
                  <div>
                    <div className="text-sm font-medium text-gray-900">{product.name}</div>
                    <div className="text-sm text-gray-500">{product.barcode}</div>
                  </div>
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">{product.category_name}</td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                  KES {product.unit_price.toFixed(2)}
                </td>
                <td className="px-6 py-4 whitespace-nowrap">
                  <div className="text-sm text-gray-900">{product.current_stock}</div>
                  {product.current_stock <= product.reorder_level && (
                    <div className="text-xs text-red-600">Low Stock</div>
                  )}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-sm font-medium">
                  <div className="flex space-x-2">
                    {user?.can_adjust_stock() && (
                      <button
                        onClick={() => {
                          setSelectedProduct(product)
                          setShowAdjustModal(true)
                        }}
                        className="text-blue-600 hover:text-blue-900"
                      >
                        <ArrowUpDown className="h-4 w-4" />
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Add Product Modal */}
      {showProductModal && (
        <div className="fixed inset-0 bg-gray-600 bg-opacity-50 flex items-center justify-center">
          <div className="bg-white p-6 rounded-lg shadow-xl max-w-md w-full">
            <h2 className="text-xl font-bold mb-4">Add New Product</h2>
            <form onSubmit={handleCreateProduct} className="space-y-4">
              <input
                type="text"
                placeholder="Product Name"
                value={productForm.name}
                onChange={(e) => setProductForm({ ...productForm, name: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                required
              />
              <input
                type="text"
                placeholder="Barcode"
                value={productForm.barcode}
                onChange={(e) => setProductForm({ ...productForm, barcode: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <select
                value={productForm.category}
                onChange={(e) => setProductForm({ ...productForm, category: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                required
              >
                <option value="">Select Category</option>
                {categories.map((category) => (
                  <option key={category.id} value={category.id}>
                    {category.name}
                  </option>
                ))}
              </select>
              <input
                type="number"
                step="0.01"
                placeholder="Unit Price"
                value={productForm.unit_price}
                onChange={(e) => setProductForm({ ...productForm, unit_price: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                required
              />
              <input
                type="number"
                step="0.01"
                placeholder="Cost Price"
                value={productForm.cost_price}
                onChange={(e) => setProductForm({ ...productForm, cost_price: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                required
              />
              <input
                type="number"
                placeholder="Reorder Level"
                value={productForm.reorder_level}
                onChange={(e) => setProductForm({ ...productForm, reorder_level: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                required
              />
              <div className="flex space-x-3">
                <button
                  type="submit"
                  disabled={loading}
                  className="flex-1 bg-blue-600 text-white py-2 px-4 rounded-md hover:bg-blue-700 disabled:opacity-50"
                >
                  {loading ? "Creating..." : "Create Product"}
                </button>
                <button
                  type="button"
                  onClick={() => setShowProductModal(false)}
                  className="flex-1 bg-gray-300 text-gray-700 py-2 px-4 rounded-md hover:bg-gray-400"
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Receive Stock Modal */}
      {showReceiveModal && (
        <div className="fixed inset-0 bg-gray-600 bg-opacity-50 flex items-center justify-center">
          <div className="bg-white p-6 rounded-lg shadow-xl max-w-2xl w-full max-h-96 overflow-y-auto">
            <h2 className="text-xl font-bold mb-4">Receive Stock</h2>
            <form onSubmit={handleReceiveStock} className="space-y-4">
              <select
                value={receiveForm.supplier_id}
                onChange={(e) => setReceiveForm({ ...receiveForm, supplier_id: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                required
              >
                <option value="">Select Supplier</option>
                {suppliers.map((supplier) => (
                  <option key={supplier.id} value={supplier.id}>
                    {supplier.name}
                  </option>
                ))}
              </select>
              <input
                type="text"
                placeholder="Invoice Number"
                value={receiveForm.invoice_number}
                onChange={(e) => setReceiveForm({ ...receiveForm, invoice_number: e.target.value })}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                required
              />

              <div className="space-y-2">
                <label className="block text-sm font-medium text-gray-700">Items</label>
                {receiveForm.items.map((item, index) => (
                  <div key={index} className="grid grid-cols-4 gap-2">
                    <select
                      value={item.product_id}
                      onChange={(e) => updateReceiveItem(index, "product_id", e.target.value)}
                      className="px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                      required
                    >
                      <option value="">Select Product</option>
                      {products.map((product) => (
                        <option key={product.id} value={product.id}>
                          {product.name}
                        </option>
                      ))}
                    </select>
                    <input
                      type="number"
                      placeholder="Quantity"
                      value={item.quantity}
                      onChange={(e) => updateReceiveItem(index, "quantity", e.target.value)}
                      className="px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                      required
                    />
                    <input
                      type="number"
                      step="0.01"
                      placeholder="Unit Cost"
                      value={item.unit_cost}
                      onChange={(e) => updateReceiveItem(index, "unit_cost", e.target.value)}
                      className="px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                      required
                    />
                    <button
                      type="button"
                      onClick={() => removeReceiveItem(index)}
                      className="px-3 py-2 bg-red-500 text-white rounded-md hover:bg-red-600"
                      disabled={receiveForm.items.length === 1}
                    >
                      Remove
                    </button>
                  </div>
                ))}
                <button
                  type="button"
                  onClick={addReceiveItem}
                  className="w-full px-3 py-2 border-2 border-dashed border-gray-300 rounded-md text-gray-500 hover:border-gray-400"
                >
                  Add Item
                </button>
              </div>

              <div className="flex space-x-3">
                <button
                  type="submit"
                  disabled={loading}
                  className="flex-1 bg-green-600 text-white py-2 px-4 rounded-md hover:bg-green-700 disabled:opacity-50"
                >
                  {loading ? "Receiving..." : "Receive Stock"}
                </button>
                <button
                  type="button"
                  onClick={() => setShowReceiveModal(false)}
                  className="flex-1 bg-gray-300 text-gray-700 py-2 px-4 rounded-md hover:bg-gray-400"
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Adjust Stock Modal */}
      {showAdjustModal && selectedProduct && (
        <div className="fixed inset-0 bg-gray-600 bg-opacity-50 flex items-center justify-center">
          <div className="bg-white p-6 rounded-lg shadow-xl max-w-md w-full">
            <h2 className="text-xl font-bold mb-4">Adjust Stock</h2>
            <p className="text-gray-600 mb-4">
              Current stock for {selectedProduct.name}: {selectedProduct.current_stock}
            </p>
            <form
              onSubmit={(e) => {
                e.preventDefault()
                const formData = new FormData(e.target as HTMLFormElement)
                const newQuantity = Number.parseInt(formData.get("new_quantity") as string)
                const reason = formData.get("reason") as string
                handleAdjustStock(selectedProduct.id, newQuantity, reason)
              }}
              className="space-y-4"
            >
              <input
                name="new_quantity"
                type="number"
                placeholder="New Quantity"
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                required
              />
              <textarea
                name="reason"
                placeholder="Reason for adjustment"
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                rows={3}
                required
              />
              <div className="flex space-x-3">
                <button type="submit" className="flex-1 bg-blue-600 text-white py-2 px-4 rounded-md hover:bg-blue-700">
                  Adjust Stock
                </button>
                <button
                  type="button"
                  onClick={() => setShowAdjustModal(false)}
                  className="flex-1 bg-gray-300 text-gray-700 py-2 px-4 rounded-md hover:bg-gray-400"
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}

export default Products
